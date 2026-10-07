"""Make src/model_deployer importable for the tests (run from deploy-job/: `pytest tests`)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
