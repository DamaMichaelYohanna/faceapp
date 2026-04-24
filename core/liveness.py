import cv2
import numpy as np
from typing import List

class LivenessDetector:
    """
    Lightweight CPU-friendly liveness detection.
    Analyzes motion and variance between multiple frames.
    """
    
    @staticmethod
    def check_liveness(frames_bytes: List[bytes], threshold: float = 0.5) -> bool:
        """
        Input: 3-5 image frames as bytes.
        Checks:
        1. Frame difference / Motion detection.
        2. Rejects if frames are too similar (indicating a static photo/video freeze).
        3. Simple texture analysis (variance) to filter out low-quality screens.
        """
        if len(frames_bytes) < 2:
            return False

        gray_frames = []
        for fb in frames_bytes:
            nparr = np.frombuffer(fb, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                gray_frames.append(gray)

        if len(gray_frames) < 2:
            return False

        # Calculate absolute difference between consecutive frames
        diffs = []
        for i in range(len(gray_frames) - 1):
            diff = cv2.absdiff(gray_frames[i], gray_frames[i+1])
            # Focus on central area where the face usually is
            h, w = diff.shape
            roi = diff[int(h*0.2):int(h*0.8), int(w*0.2):int(w*0.8)]
            diff_mean = np.mean(roi)
            diffs.append(diff_mean)

        mean_diff = np.mean(diffs)
        
        # LIVENESS LOGIC:
        # 1. If mean_diff is extremely low (e.g. < 0.1), frames are too identical (likely a photo/static).
        # 2. If mean_diff is moderate, it indicates micro-movements (blinking, breathing).
        # 3. If mean_diff is extremely high, it might be excessive motion or spoofing.
        
        # PRODUCTION SCALE: Adjust these constants based on field tests
        STILL_PHOTO_THRESHOLD = 0.15
        EXCESSIVE_MOTION_THRESHOLD = 15.0 # Too much motion might be a tablet being moved
        
        if mean_diff < STILL_PHOTO_THRESHOLD:
            return False # Too static
            
        if mean_diff > EXCESSIVE_MOTION_THRESHOLD:
            return False # Too chaotic
            
        # Additional check: Laplacian variance for focus/texture
        for frame in gray_frames:
            variance = cv2.Laplacian(frame, cv2.CV_64F).var()
            if variance < 50: # Very blurry images often indicate a photo of a photo
                return False

        return True
