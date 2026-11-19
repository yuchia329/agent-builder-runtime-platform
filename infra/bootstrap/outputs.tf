output "state_bucket" {
  description = "Bucket holding every environment's state."
  value       = aws_s3_bucket.state.bucket
}

output "backend_hcl" {
  description = "Contents for envs/<env>/backend.hcl (git-ignored)."
  value       = "bucket = \"${aws_s3_bucket.state.bucket}\"\n"
}
