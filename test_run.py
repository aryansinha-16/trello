"""Run both digest jobs immediately for testing."""
import sys
sys.path.insert(0, '.')
from main import job_ravi_digest, job_sonal_digest

print("=== Running Job 1: Ravi Digest ===")
job_ravi_digest()

print("\n=== Running Job 2: Sonal Digest ===")
job_sonal_digest()
