import unittest
from tools.target_quality import TargetQuality

def t(x, area=1000): return {"target_id":1,"center":[x,100],"area":area,"valid":True,"status":"confirmed"}

class QualityTests(unittest.TestCase):
    def test_requires_stable_frames_and_returns_pixel_data(self):
        q=TargetQuality(3,5,.3)
        self.assertFalse(q.evaluate(t(10),now_ms=0,observed_ms=0)["valid"])
        self.assertFalse(q.evaluate(t(11),now_ms=1,observed_ms=1)["valid"])
        r=q.evaluate(t(10),now_ms=2,observed_ms=2)
        self.assertEqual(r["quality_state"],"STABLE"); self.assertTrue(r["valid"])
        self.assertEqual(r["center_px"],[10.0,100.0])
    def test_jitter_timeout_and_invalid_clear_coordinates(self):
        q=TargetQuality(3,1,.3)
        for x in (0,10,0): r=q.evaluate(t(x),now_ms=0,observed_ms=0)
        self.assertFalse(r["valid"]); self.assertIsNone(r["center_px"])
        r=q.evaluate(t(0),now_ms=300,observed_ms=0)
        self.assertEqual(r["reason"],"detection_timeout"); self.assertIsNone(r["center_px"])
    def test_unconfirmed_is_searching(self):
        r=TargetQuality().evaluate({"valid":False},now_ms=0,observed_ms=0)
        self.assertEqual(r["quality_state"],"SEARCHING")
if __name__ == '__main__': unittest.main()
