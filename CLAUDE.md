# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application
```bash
# Install dependencies
pip install -r requirements.txt

# Run the main application
python main.py
```

### Code Quality Checks
```bash
# Type checking with mypy
mypy *.py --config-file mypy.ini \
  --allow-untyped-defs \
  --disable-error-code=no-untyped-def \
  --disable-error-code=no-untyped-call

# Style checking with flake8
flake8 *.py --max-line-length=100 --exclude=test_*.py --ignore=F401,F403

# Security checks with bandit
bandit -r . -f json -o bandit-report.json || echo "Security check completed"

# Run tests (when available)
pytest --cov=./ --cov-report=xml --cov-report=term-missing
```

### Docker Operations
```bash
# Build Docker image
docker build -t lesson-plan-manager .

# Run Docker container
docker run -p 80:80 --env-file .env lesson-plan-manager
```

## Architecture Overview

### Core Components

1. **main.py** - Flask application entry point that:
   - Manages periodic plan checking cycles
   - Hosts RESTful API endpoints
   - Coordinates all managers for different lesson plans
   - Handles MongoDB connections and configuration loading

2. **LessonPlan.py** - Main lesson plan manager that:
   - Downloads and processes Excel lesson plans from PUW
   - Manages plan checking schedules
   - Handles Discord notifications
   - Stores processed plans in MongoDB

3. **MoodleParserComponent.py** - Parses Moodle course pages to:
   - Extract assignments, resources, and activities
   - Format activity data with AI (optional)
   - Store parsed activities in MongoDB

4. **comparer.py** - AI-powered plan comparison that:
   - Compares new plans with previous versions
   - Uses OpenRouter API for intelligent change detection
   - Stores comparison results in MongoDB

5. **routes/** - Modular API endpoints:
   - `status.py` - System status and maintenance mode
   - `config.py` - Configuration management
   - `plans.py` - Plan retrieval and manual checks
   - `activities.py` - Moodle activity queries
   - `comparisons.py` - Historical comparison results
   - `suggestions.py` - User feedback system
   - `logs.py` - System logs retrieval

### Data Flow

1. **Plan Processing**:
   - Downloads Excel files from configured URLs (requires PUW credentials)
   - Parses specific sheets and groups within the Excel
   - Converts to HTML format for display
   - Stores in MongoDB collections named after the plan

2. **Change Detection**:
   - Compares new plans with cached versions
   - If changes detected and AI comparison enabled, sends to OpenRouter
   - Optionally sends Discord notifications with comparison results

3. **Moodle Integration**:
   - Selenium-based scraping of Moodle course pages
   - Parses activities into structured data
   - Optional AI formatting for better readability

### MongoDB Collections

- **plans_config** - Plan configurations (document _id: "plans_json")
- **discord_config** - Discord webhook configurations per plan
- **system_config** - System-wide settings
- **logs** - Check cycle logs
- **suggestions** - User feedback
- **activities** - Parsed Moodle activities
- **[plan_name]** - Individual plan collections containing:
  - Processed plan data (groups as documents)
  - Comparison results in "comparisons" field

### Configuration Structure

Plans are configured in MongoDB or via PLANS_JSON_URL with this structure:
```json
{
  "plan_id": {
    "name": "Display name",
    "faculty": "Faculty name",
    "category": "st|nst|nst-online",
    "download_url": "Excel file URL",
    "sheet_name": "Excel sheet to parse",
    "groups": {
      "Group Name": "Excel column identifier"
    },
    "compare": true/false,  // Enable AI comparison
    "notify": true/false    // Enable Discord notifications
  }
}
```

## Key Environment Variables

Required:
- `EMAIL` - PUW login email
- `PASSWORD` - PUW password
- `MONGO_URI` - MongoDB connection string
- `MONGO_DB` - Database name

Optional:
- `OPENROUTER_API_KEY` - For AI comparisons
- `MOODLE_URL` - Moodle course page to parse
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` - CloudWatch logging
- `PUSHOVER_KEY`, `PUSHOVER_USER` - Suggestion notifications