variable "aws_profile" {
  description = "AWS CLI profile (credentials stay in ~/.aws). The backend block names it too."
  type        = string
  default     = "default"

  validation {
    condition     = var.aws_profile != "prod"
    error_message = "This project never deploys with the prod profile."
  }
}

variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Value of the project tag on every resource; the project budget filters on it."
  type        = string
  default     = "agent-platform"
}

# --- Compute (M6 task 7). Personal values go in terraform.tfvars (git-ignored; see the example).

variable "tenant_id" {
  description = "The one tenant (control plane TENANT_ID; the GitHub App credential is stored under it)."
  type        = string
  default     = "tenant-demo"
}

variable "slack_approval_channel" {
  description = "Slack channel id for approval requests (SLACK_APPROVAL_CHANNEL; required with SSM secrets)."
  type        = string

  validation {
    condition     = can(regex("^[CG][A-Z0-9]{8,}$", var.slack_approval_channel))
    error_message = "slack_approval_channel must be a Slack channel id like C0123456789."
  }
}

variable "github_oauth_client_id" {
  description = "The GitHub App's client id (GITHUB_OAUTH_CLIENT_ID); its secret is in SSM."
  type        = string
}

variable "gateway_connectors" {
  description = "Connectors the gateway loads (CONNECTORS): fake for stub checks, github to go live."
  type        = string
  default     = "fake"
}

variable "github_repos" {
  description = "Repos the github connector may touch (GITHUB_REPOS, owner/name,...); needed with github."
  type        = string
  default     = ""
}

variable "worker_image_tag" {
  description = "Tag of the worker image in ECR (the commit it was built from; make worker-image)."
  type        = string
}

variable "worker_max_tasks" {
  description = "Most worker tasks the service scales to."
  type        = number
  default     = 2
}

variable "worker_model_env" {
  description = "Model provider settings for the worker (not secrets; API keys come from SSM)."
  type        = map(string)
  default = {
    MODEL_PROVIDERS          = "minimax"
    MODEL_MINIMAX_PROTOCOL   = "openai"
    MODEL_MINIMAX_BASE_URL   = "https://api.minimax.io/v1"
    MODEL_MINIMAX_EXTRA_BODY = "{\"reasoning_split\":true}"
  }
}

variable "control_plane_memory_mb" {
  description = "Control plane Lambda memory: 1769 MB is one vCPU, the smallest that answers a cold Slack click in time (ADR 0017)."
  type        = number
  default     = 1769
}

variable "gateway_memory_mb" {
  description = "Gateway Lambda memory (152 MB used in the trial, ADR 0019)."
  type        = number
  default     = 512
}

variable "web_adapter_layer_version" {
  description = "Version of AWS's LambdaAdapterLayerArm64 (ADR 0019); upgrade on purpose."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps the services' logs."
  type        = number
  default     = 14
}

# --- Protection (M6 task 13).

variable "alert_email" {
  description = "Address that receives alarm emails (SNS; confirm the subscription once). Set in terraform.tfvars (git-ignored)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "model_spend_alarm_usd" {
  description = "Email when model spend in the last 24 hours goes above this many US dollars."
  type        = number
  default     = 1
}

variable "api_throttle_rate" {
  description = "Steady requests per second each API route accepts before answering 429."
  type        = number
  default     = 5
}

variable "load_test" {
  description = "Load-test mode (M7, load_test.tf): load_test_settings replace the throttle and worker limit, the gateway runs the fake connector, the stub model waits. Set back to false after a test."
  type        = bool
  default     = false
}

variable "load_test_settings" {
  description = "Values used while load_test is true."
  type = object({
    api_throttle_rate  = number # requests per second per route (/mcp needs about 100 at 80 runs at once)
    api_throttle_burst = number
    worker_max_tasks   = number # 0.5 vCPU each; the account's Fargate limit is 64 vCPU
    stub_latency_ms    = number # MODEL_STUB_LATENCY_MS: each stub model call waits this long
  })
  default = {
    api_throttle_rate  = 200
    api_throttle_burst = 400
    worker_max_tasks   = 20
    stub_latency_ms    = 2000
  }

  validation {
    condition     = var.load_test_settings.worker_max_tasks <= 100 && var.load_test_settings.stub_latency_ms <= 60000
    error_message = "worker_max_tasks at most 100 (Fargate quota), stub_latency_ms at most 60000 (the worker's limit)."
  }
}

variable "api_throttle_burst" {
  description = "Requests each API route accepts at once before answering 429."
  type        = number
  default     = 10
}
