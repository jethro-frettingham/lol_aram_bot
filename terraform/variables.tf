##############################################################################
# Variables
##############################################################################

variable "aws_region" {
  description = "AWS region to deploy into. ap-southeast-2 = Sydney (closest to OCE)"
  type        = string
  default     = "ap-southeast-2"
}

variable "lol_region" {
  description = "League of Legends region code. Options: OCE, NA, EUW, EUNE, KR"
  type        = string
  default     = "OCE"

  validation {
    condition     = contains(["OCE", "NA", "EUW", "EUNE", "KR"], var.lol_region)
    error_message = "Must be one of: OCE, NA, EUW, EUNE, KR"
  }
}

variable "poll_interval_minutes" {
  description = "How often (in minutes) to check for new games."
  type        = number
  default     = 10

  validation {
    condition     = var.poll_interval_minutes >= 5
    error_message = "Polling more frequently than every 5 minutes risks hitting Riot API rate limits."
  }
}

variable "tracked_players" {
  description = <<-EOT
    Comma-separated list of Riot IDs to track, in GameName#TAG format.
    Example: "CoolSummoner#OCE1,AnotherFriend#OCE1,ThirdMate#EUW"
  EOT
  type    = string
  # No default – you MUST provide this.
}

variable "riot_api_key" {
  description = "Riot Games API key (from https://developer.riotgames.com/). Mark as sensitive."
  type        = string
  sensitive   = true
}

variable "discord_webhook_url" {
  description = "Discord channel webhook URL. Create in: Server Settings → Integrations → Webhooks."
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key (from https://console.anthropic.com/)."
  type        = string
  sensitive   = true
}
