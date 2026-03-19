# 🎮 ARAM Mayhem Match Review Bot

A Discord bot that auto-posts match reviews after your ARAM games — stats, augments, damage breakdowns, and spicy AI commentary powered by Claude.

## What it posts

For every new ARAM / Mayhem game involving your tracked summoners, the bot sends a Discord embed with:

- 🏆 / 💀  Victory or Defeat banner + game duration
- Per-player breakdown:
  - ⚔️ KDA (with ratio)
  - 🔥 Damage dealt to champions
  - 🛡️ Damage taken
  - 🔰 Self-mitigated damage
  - 💚 Healing (if relevant)
  - 💰 Gold earned
  - ✨ Augments selected (Mayhem/Arena modes)
  - 🤖 AI-generated witty comment from Claude
- 🔗 Direct link to op.gg full match stats

---

## Architecture

```
EventBridge (schedule)
      │  every 10 min
      ▼
  AWS Lambda (Python 3.12)
      │
      ├── Riot API  →  fetch recent ARAM matches
      ├── DynamoDB  →  dedup (skip already-posted games)
      ├── Claude API →  generate per-player commentary
      └── Discord Webhook →  post embed
```

**Cost: ~$0/month** at friend-group scale (all services within free tier).

| Service | Usage | Cost |
|---|---|---|
| Lambda | ~4,000 invocations/month | Free tier |
| EventBridge | ~4,000 events/month | Free |
| DynamoDB | On-demand, tiny table | Free tier |
| SSM Parameter Store | 3 SecureString params | Free |
| CloudWatch Logs | 7-day retention | Free tier |
| Riot API | Personal key | Free |
| Claude API | ~$0.003/match (Sonnet) | Pay as you go |

Claude API cost: roughly 3 cents per 10 matches posted.

---

## Prerequisites

1. **AWS account** with CLI configured (`aws configure`)
2. **Terraform** ≥ 1.5 ([install](https://developer.hashicorp.com/terraform/install))
3. **Riot API key** — [developer.riotgames.com](https://developer.riotgames.com/)
   - Development keys expire every 24 hours. For persistent use, register a personal app to get a production key.
4. **Discord Webhook URL** — Server Settings → Integrations → Webhooks → New Webhook
5. **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com/settings/keys)

---

## Setup

### 1. Clone and configure

```bash
git clone <this-repo>
cd lol-aram-bot/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
tracked_players     = "YourName#OCE1,FriendOne#OCE1,FriendTwo#OCE1"
riot_api_key        = "RGAPI-..."
discord_webhook_url = "https://discord.com/api/webhooks/..."
anthropic_api_key   = "sk-ant-api03-..."
```

### 2. Deploy

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Terraform will:
- Create the Lambda function (auto-packages Python code)
- Set up EventBridge to trigger it every 10 minutes
- Create the DynamoDB dedup table
- Store your secrets in SSM Parameter Store (encrypted)

### 3. Test it manually

After applying, run the output command to manually invoke the bot:

```bash
aws lambda invoke \
  --function-name aram-bot \
  --region ap-southeast-2 \
  /tmp/bot-response.json && cat /tmp/bot-response.json
```

### 4. Watch the logs

```bash
aws logs tail /aws/lambda/aram-bot --follow --region ap-southeast-2
```

---

## Configuration

### Change tracked players

Edit `tracked_players` in `terraform.tfvars` and re-run `terraform apply`.

### Change poll interval

Edit `poll_interval_minutes` (minimum 5) and re-apply.

### Rotate API keys

```bash
aws ssm put-parameter \
  --name "/aram-bot/riot-api-key" \
  --value "RGAPI-new-key-here" \
  --type SecureString \
  --overwrite \
  --region ap-southeast-2
```

### Enable/disable the bot

```bash
# Pause
aws events disable-rule --name aram-bot-schedule --region ap-southeast-2

# Resume
aws events enable-rule --name aram-bot-schedule --region ap-southeast-2
```

---

## Supported queue types

The bot watches these queue IDs automatically:

| ID | Mode |
|---|---|
| 450 | Standard ARAM |
| 900 | ARAM Mayhem (URF) |
| 1020 | One for All |
| 1300 | Nexus Blitz |

To add more, edit `MAYHEM_QUEUE_IDS` in `lambda/src/index.py`.

---

## Troubleshooting

**Bot isn't posting anything**
- Check logs: `aws logs tail /aws/lambda/aram-bot --follow`
- Verify your Riot ID format is `GameName#TAG` (not summoner name)
- Development API keys expire every 24h — renew at developer.riotgames.com

**`403 Forbidden` from Riot API**
- Your API key has expired or is invalid. Get a new one and update SSM.

**`404` when resolving summoner**
- Double-check the `#TAG` portion. For OCE players it's usually `#OCE1`.

**Duplicate posts**
- Should not happen — the DynamoDB dedup table prevents this. Check that the Lambda has DynamoDB write permissions.

---

## Teardown

```bash
terraform destroy
```

This removes all AWS resources. The DynamoDB table (and its dedup history) will be deleted.
