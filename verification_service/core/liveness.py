import cv2
import numpy as np
from typing import List


class LivenessDetector:
    """
    Simple passive liveness detector based on multi-frame variance analysis.
    Checks that frames show natural variation (blinking / slight head movement).
    """

    @staticmethod
    def check_liveness(frames_content: List[bytes]) -> bool:
        """
        Return True if the sequence of frames appears to be from a live subject.
        Falls back to True when fewer than 2 frames are provided.
        """
        if len(frames_content) < 2:
            return True

        gray_frames = []
        for content in frames_content:
            nparr = np.frombuffer(content, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                gray_frames.append(img)

        if len(gray_frames) < 2:
            return True

        h, w = gray_frames[0].shape
        resized = [cv2.resize(f, (w, h)) for f in gray_frames]

        stack = np.stack(resized, axis=0).astype(np.float32)
        variance = np.var(stack, axis=0)
        mean_variance = float(np.mean(variance))

        import os
        threshold = float(os.getenv("LIVENESS_VARIANCE_THRESHOLD", "10.0"))
        return mean_variance > threshold
