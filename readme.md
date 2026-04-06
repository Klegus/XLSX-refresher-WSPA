# Lesson Plan Manager - Backend
## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [API Endpoints](#api-endpoints)
- [Contributing](#contributing)
- [License](#license)

## Overview

Lesson Plan Manager is a Python application designed to automate the tracking and management of university lesson plans and Moodle activities. It periodically checks for updates to specified lesson plans, compares them with previous versions using AI, parses Moodle activities, and can notify users of changes via Discord webhooks. It also provides a RESTful API for querying plans, activities, logs, and system status.

## Features

- 🔄 **Automatic Plan Checking**: Periodically downloads and processes lesson plans from configured sources.
- 📊 **AI-Powered Plan Comparison**: Compares new plans with previous versions to detect significant changes using an AI model (via OpenRouter).
- 🎓 **Moodle Activity Parsing**: Downloads and parses activities (assignments, resources, etc.) from a configured Moodle page.
- 💾 **Database Storage**: Stores processed plans, activities, comparison results, and logs in MongoDB.
- 🔔 **Discord Notifications**: Sends notifications about plan updates (and optionally comparison results) to a configured Discord webhook.
- 🌐 **RESTful API**: Provides endpoints to query system status, configuration, plans, activities, logs, and more.
- 📅 **Multiple Plan Support**: Manages configurations for multiple lesson plans across different faculties and study modes (standard, non-standard).
- ⚙️ **Dynamic Configuration**: Plan configurations can be loaded from MongoDB or a remote JSON URL.
- 🛠️ **Maintenance Mode**: Allows administrators to put the system into maintenance mode via API.
- 📄 **Logging**: Comprehensive logging to console, file, and AWS CloudWatch (optional).
- ✨ **Suggestion Box**: API endpoint for users to submit suggestions.

## Requirements

- Python 3.12+
- MongoDB Instance
- Required Python packages (see `requirements.txt`)

**Optional:**
- Discord Webhook URL (for notifications)
- OpenRouter API Key (for plan comparison and Moodle content formatting)
- AWS Credentials (for CloudWatch logging)
- Pushover Credentials (for suggestion notifications)

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/Klegus/XLSX-refresher-WSPA.git # Replace with your repo URL
    cd XLSX-refresher-WSPA
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Set up environment variables**: Create a `.env` file in the root directory (see [Configuration](#configuration)).

## Configuration

Create a `.env` file in the root directory with the following variables. Adjust values according to your setup.

```dotenv
# Core Credentials
EMAIL=your_puw_login_email@example.com   # Login for the plan source (e.g., PUW)
PASSWORD=your_puw_password              # Password for the plan source
MONGO_URI=mongodb://user:pass@host:port/db_name # MongoDB connection string
MONGO_DB=Lesson_dev                     # MongoDB database name

# Plan Configuration Source
PLANS_JSON_URL=https://example.com/path/to/your/plans.json # URL to fetch plan configurations if not in DB

# Moodle Configuration
MOODLE_URL=https://moodle.example.com/course/view.php?id=123 # URL of the Moodle course page to parse

# AI Comparison (Optional)
OPENROUTER_API_KEY=your_openrouter_api_key # Required if "compare": true in plans.json
SELECTED_MODEL=openai/gpt-3.5-turbo       # AI model for comparison (e.g., openai/gpt-4o-latest)

# Discord Notifications (Optional)
# Note: Webhook URLs are now configured per-plan within the MongoDB plan collection (_id: discord_config)
# DISCORD_WEBHOOK_URL=your_discord_webhook_url # This is likely deprecated, check plan-specific config

# AWS CloudWatch Logging (Optional)
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
AWS_REGION=eu-west-1                 # Your AWS region

# File Logging (Optional)
LOG_TO_FILE=true                     # Set to true to enable file logging
LOG_DIR=logs                         # Directory to store log files

# Excel Processing Optimization (Optional)
CLEAN_EXCEL_FILE=false               # Enable only when specific plans require aggressive cleanup

# Pushover Notifications for Suggestions (Optional)
PUSHOVER_KEY=your_pushover_api_token_or_user_key
PUSHOVER_USER=your_pushover_user_key # Only needed if PUSHOVER_KEY is an app token

# Other Settings
PLANS_DIRECTORY=lesson_plans         # Directory to store downloaded/processed plan files (temporary)
```

**Plan Configuration (`plans.json` or MongoDB `plans_config` collection):**
The structure defining which plans to check is managed either via the `PLANS_JSON_URL` or directly in the `plans_config` collection in MongoDB (document `_id: "plans_json"`). Example structure:
```json
{
  "plan_id_1": {
    "name": "Computer Science Year 1",
    "faculty": "Informatics",
    "category": "st", // st, nst, nst-online
    "download_url": "https://puw.wspa.pl/...",
    "sheet_name": "Plan",
    "groups": {
      "Group 1A": "I W IiS1",
      "Group 1B": "I W IiS2"
    },
    "compare": true, // Enable AI comparison (requires OpenRouter key)
    "notify": true   // Enable Discord notifications (requires webhook in plan's DB collection)
  },
  "plan_id_2": { ... }
}
```

## Usage

To start the Lesson Plan Manager service:

```bash
python main.py
```

This will:
1.  Load configuration.
2.  Initialize managers for each lesson plan defined in the configuration.
3.  Start the Flask server for API endpoints (typically on port 80, check `main.py`).
4.  Begin the main loop, periodically checking for plan updates, Moodle activity updates, and performing comparisons.

## Test Stack (Docker Compose)

For isolated local testing, use the dedicated test stack (`MongoDB + backend`) from this repository root:

```bash
docker compose -f docker-compose.test.yml up -d --build
```

Useful commands:

```bash
# follow logs
docker compose -f docker-compose.test.yml logs -f

# quick API smoke check
curl -sS http://localhost:5006/api/status
curl -sS http://localhost:5006/api/config

# stop stack
docker compose -f docker-compose.test.yml down
```

Or use helper script:

```bash
./scripts/test-stack.sh up
./scripts/test-stack.sh status
./scripts/test-stack.sh smoke
./scripts/test-stack.sh logs
./scripts/test-stack.sh down
```

Notes:
- The test stack uses `.env.test` and MongoDB database `Lesson_test`.
- Mongo is pre-seeded with a minimal `system_config` and empty `plans_config`.
- No production credentials are required.

## API Endpoints

The application provides the following RESTful API endpoints:

**Status & Configuration:**

-   `GET /api/status`: Returns the current operational status, maintenance mode, and last check details.
-   `GET /api/config`: Retrieves the current system and plans configuration.
-   `POST /api/config`: Updates the system and/or plans configuration.
-   `PUT /api/config`: Updates specific plans within the plans configuration.
-   `POST /api/maintenance-toggle`: Enables or disables maintenance mode.

**Plans & Groups:**

-   `GET /api/faculties/<category>`: Lists unique faculties for a given category (`st`, `nst`, `nst-online`).
-   `GET /api/plans/<category>/<faculty>`: Lists plans available for a specific category and faculty.
-   `GET /api/plan/<collection_name>`: Retrieves the latest processed plan data for the entire collection.
-   `GET /api/plan/<collection_name>/<group_name>`: Retrieves the latest processed plan HTML for a specific group within a collection.
-   `POST /api/check-plan/<plan_id>`: Manually triggers an update check for a specific plan ID.

**Activities (Moodle):**

-   `GET /api/activities`: Retrieves parsed Moodle activities, supports pagination (`skip`, `limit`) and date filtering (`start_date`, `end_date`).

**Comparisons:**

-   `GET /api/comparisons/<collection_name>/<group_name>`: Retrieves historical AI comparison results for a specific plan group.

**Logs:**

-   `GET /api/logs`: Retrieves recent check cycle logs from the database.

**Suggestions:**

-   `POST /api/suggestions`: Submits a new suggestion.
-   `GET /api/suggestions`: Retrieves submitted suggestions (supports `status`, `skip`, `limit` query params).
-   `PATCH /api/suggestions/<suggestion_id>`: Updates the status (`pending`, `approved`, `rejected`) of a suggestion.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an issue.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

Made with ❤️ by Klegus
