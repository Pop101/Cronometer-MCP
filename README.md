# Cronometer MCP

**Cronometer nutrition tracking via MCP — no Gold subscription required.** Uses the mobile REST API to search foods, log meals, track macros, and manage fasting data through the Model Context Protocol.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)


## Features

- **No Gold Required** — Uses the free mobile API (`mobile.cronometer.com`)
- **Full Nutrition Tracking** — Search foods, log meals, track macros and micronutrients
- **Fasting Support** — View fasting history and statistics
- **MCP Integration** — Works with any MCP-compatible AI agent (Hermes, Claude, etc.)
- **Secure** — Credentials stored locally, never transmitted to third parties


## Install

```bash
git clone git@github.com:Pop101/Cronometer-MCP.git
cd Cronometer-MCP
pip install -e .
```

Requires Python 3.11+ and a Cronometer account (free tier works).


## Quick Start

1. **Set your credentials:**
   ```bash
   export CRONOMETER_USERNAME="your-email@example.com"
   export CRONOMETER_PASSWORD="your-password"
   ```

2. **Run the MCP server:**
   ```bash
   python cronometer_mcp_server.py
   ```

3. **Connect via MCP client:**
   The server exposes 11 tools for nutrition tracking.


## MCP Tools

| Tool | Description |
|------|-------------|
| `search_foods` | Search Cronometer food database by name |
| `get_food_details` | Get full nutrition profile and serving sizes |
| `get_food_log` | Get diary entries for a date (defaults to today) |
| `get_daily_nutrition` | Get daily macro and micronutrient totals |
| `get_nutrition_scores` | Get nutrition category scores (Vitamins, Minerals, etc.) |
| `add_food_entry` | Log a food serving to your diary |
| `mark_day_complete` | Mark a diary day as complete or incomplete |
| `copy_day` | Copy entries from previous day to given date |
| `get_macro_targets` | Get weekly macro schedule and saved target templates |
| `get_fasting_history` | View fasts within a date range |
| `get_fasting_stats` | Aggregate fasting statistics |


## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `CRONOMETER_USERNAME` | Your Cronometer email | Yes |
| `CRONOMETER_PASSWORD` | Your Cronometer password | Yes |


### MCP Client Configuration

#### Hermes

```bash
hermes mcp add cronometer --command python --args /path/to/cronometer_mcp_server.py
```

#### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cronometer": {
      "command": "python",
      "args": ["/path/to/cronometer_mcp_server.py"],
      "env": {
        "CRONOMETER_USERNAME": "your-email@example.com",
        "CRONOMETER_PASSWORD": "your-password"
      }
    }
  }
}
```


## Usage Examples

### Search Foods
```
Search Cronometer for "chicken breast"
```

### Log a Meal
```
Log 200g of chicken breast to lunch today
```

### Check Daily Nutrition
```
What are my macro totals for today?
```

### View Fasting History
```
Show my fasting stats for the last 30 days
```


## How It Works

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  MCP Client     │───▶│  Cronometer MCP  │───▶│  Cronometer     │
│  (Hermes, etc.) │    │  Server          │    │  Mobile API     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │
                              ▼
                       ┌──────────────────┐
                       │  Local Storage   │
                       │  (credentials)   │
                       └──────────────────┘
```

The server authenticates with Cronometer's mobile API using standard email/password login. All data stays local — credentials are never sent to third parties.


## API Details

This MCP server uses Cronometer's mobile REST API (`mobile.cronometer.com`), which is available to all users without a Gold subscription. The API provides:

- Food search with nutritional data
- Diary management (add, remove, copy entries)
- Macro and micronutrient tracking
- Fasting history and statistics
- Nutrition scores and targets


## Security

- **Local-first**: All data stays on your machine
- **No telemetry**: No usage data collected or transmitted
- **Secure storage**: Credentials stored in environment variables or local files
- **Standard auth**: Uses Cronometer's official authentication flow


## Troubleshooting

### Login Failures

If you get "Login failed" errors:
1. Verify your credentials are correct
2. Check if you're rate-limited (wait a few minutes)
3. Ensure your Cronometer account is active

### Rate Limiting

The API may rate-limit after many rapid requests. The server includes retry logic with exponential backoff.

### Connection Issues

If the server can't connect to Cronometer:
1. Check your internet connection
2. Verify `mobile.cronometer.com` is accessible
3. Check for any firewall or proxy restrictions


## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request


## License

All rights reserved.


## Acknowledgments

- Built with [FastMCP](https://github.com/jlowin/fastmcp)
- Uses [httpx](https://github.com/encode/httpx) for HTTP requests
- Inspired by the need for free nutrition tracking in AI agents