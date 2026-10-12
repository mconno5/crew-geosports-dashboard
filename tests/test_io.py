import tempfile
from pathlib import Path
import unittest

from geosports.io import read_scores_csv


class ScoreCsvTests(unittest.TestCase):
    def test_input_csv_rejects_out_of_range_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scores.csv"
            path.write_text(
                "timestamp,sender,score,emoji_row\n"
                "2026-10-12 10:00:00,mark,0,\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                read_scores_csv(path)


if __name__ == "__main__":
    unittest.main()
