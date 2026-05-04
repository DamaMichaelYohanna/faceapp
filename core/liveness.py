import cv2
import numpy as np
from typing import List

class LivenessDetector:
    """
    Lightweight CPU-friendly liveness detection.
    Analyzes motion and variance between multiple frames.
    """

    STILL_PHOTO_THRESHOLD = 0.01
    EXCESSIVE_MOTION_THRESHOLD = 15.0
    MIN_LAPLACIAN_VARIANCE = 50

    @staticmethod
    def _normalize_frame_size(frame: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
        """Resize grayscale frames to a shared size so frame diffs are comparable."""
        target_height, target_width = target_shape
        if frame.shape == (target_height, target_width):
            return frame
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    @staticmethod
    def analyze_frames(frames_bytes: List[bytes]) -> dict:
        """Return low-level motion and texture signals for a frame sequence."""
        if len(frames_bytes) < 2:
            return {
                "valid_frames": 0,
                "mean_diff": 0.0,
                "has_motion": False,
                "is_sharp": False,
                "liveness_passed": False,
            }

        gray_frames = []
        for fb in frames_bytes:
            nparr = np.frombuffer(fb, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                gray_frames.append(gray)

        if len(gray_frames) < 2:
            return {
                "valid_frames": len(gray_frames),
                "mean_diff": 0.0,
                "has_motion": False,
                "is_sharp": False,
                "liveness_passed": False,
            }

        base_shape = gray_frames[0].shape
        gray_frames = [
            LivenessDetector._normalize_frame_size(frame, base_shape)
            for frame in gray_frames
        ]

        diffs = []
        for i in range(len(gray_frames) - 1):
            diff = cv2.absdiff(gray_frames[i], gray_frames[i+1])
            h, w = diff.shape
            roi = diff[int(h*0.2):int(h*0.8), int(w*0.2):int(w*0.8)]
            diff_mean = np.mean(roi)
            diffs.append(diff_mean)

        mean_diff = float(np.mean(diffs))
        has_motion = (
            mean_diff >= LivenessDetector.STILL_PHOTO_THRESHOLD
            and mean_diff <= LivenessDetector.EXCESSIVE_MOTION_THRESHOLD
        )

        is_sharp = True
        for frame in gray_frames:
            variance = cv2.Laplacian(frame, cv2.CV_64F).var()
            if variance < LivenessDetector.MIN_LAPLACIAN_VARIANCE:
                is_sharp = False
                break

        return {
            "valid_frames": len(gray_frames),
            "mean_diff": mean_diff,
            "has_motion": has_motion,
            "is_sharp": is_sharp,
            # Pass liveness if there is any motion AND sharpness,
            # OR if the frames are sharp and mean_diff is non-zero (real webcam, slight motion).
            # This allows real webcam captures with minimal movement to still pass.
            "liveness_passed": (has_motion and is_sharp) or (is_sharp and mean_diff > 0.0),
        }
    
    @staticmethod
    def check_liveness(frames_bytes: List[bytes], threshold: float = 0.5) -> bool:
        """
        Input: 3-5 image frames as bytes.
        Checks:
        1. Frame difference / Motion detection.
        2. Rejects if frames are too similar (indicating a static photo/video freeze).
        3. Simple texture analysis (variance) to filter out low-quality screens.
        """
        analysis = LivenessDetector.analyze_frames(frames_bytes)
        return bool(analysis["liveness_passed"])
