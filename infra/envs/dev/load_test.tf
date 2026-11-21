# Load-test mode (M7): one switch, load_test = true in terraform.tfvars, for the stub-model load
# tests in loadtest/. It raises the API throttle, the worker limit and the runs each worker
# executes at once (so 1,000 runs can execute together), puts the gateway on the
# fake connector (never real GitHub under load) and slows the stub model to a fixed latency.
# M8: a share of stub calls answers as still rate limited (runs are set aside, ADR 0027) and each
# stub call has a small price (daily spend caps, ADR 0026).
# load_test = false restores every normal value with one apply. While on, the live issue-triage
# still answers real GitHub issues, but with fake tools (nothing is written to GitHub).

locals {
  load_test = var.load_test

  api_throttle_rate  = local.load_test ? var.load_test_settings.api_throttle_rate : var.api_throttle_rate
  api_throttle_burst = local.load_test ? var.load_test_settings.api_throttle_burst : var.api_throttle_burst
  worker_max_tasks   = local.load_test ? var.load_test_settings.worker_max_tasks : var.worker_max_tasks
  worker_concurrency = local.load_test ? var.load_test_settings.worker_concurrency : var.worker_concurrency
  gateway_connectors = local.load_test ? "fake" : var.gateway_connectors
  # GITHUB_REPOS only for the github connector.
  gateway_github_repos = strcontains(local.gateway_connectors, "github") ? var.github_repos : ""
  # Added to the worker's environment in load mode only (0 = no wait otherwise).
  worker_load_env = local.load_test ? {
    MODEL_STUB_LATENCY_MS = tostring(var.load_test_settings.stub_latency_ms)
    MODEL_STUB_429_RATE   = tostring(var.load_test_settings.stub_429_rate)
    MODEL_STUB_PRICE_USD  = tostring(var.load_test_settings.stub_price_usd)
  } : {}
}
