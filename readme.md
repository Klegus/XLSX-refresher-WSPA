# Lesson Plan Manager

![Lesson Plan Manager Logo](https://via.placeholder.com/150)

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

Lesson Plan Manager is a robust Python application designed to automate the process of tracking and managing lesson plans. It periodically checks for updates to lesson plans, compares them with previous versions, and notifies users of any changes via Discord webhooks. The application also provides a RESTful API for querying current and upcoming lessons.

## Features

- 🔄 Automatic lesson plan updates
- 📊 Plan comparison and change detection
- 🔔 Discord notifications for plan changes
- 🌐 RESTful API for lesson queries
- 📅 Support for multiple group schedules
- 🕒 Custom time setting for testing purposes
- 📁 Automatic file management and cleaning

## Requirements

- Python 3.7+
- MongoDB
- Discord Webhook (optional, for notifications)

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/lesson-plan-manager.git
   cd lesson-plan-manager
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Set up your environment variables (see [Configuration](#configuration) section).

## Configuration

Create a `.env` file in the root directory with the following variables:

```
EMAIL=your_email@example.com
PASSWORD=your_password
MONGO_URI=your_mongodb_connection_string
OPENROUTER_API_KEY=your_openrouter_api_key
SELECTED_MODEL=your_selected_model
DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

Adjust the values according to your setup.

## Usage

To start the Lesson Plan Manager:

```
python main.py
```

This will initiate the periodic checking of lesson plans and start the Flask server for API endpoints.

## API Endpoints

### Get Current Status
- **URL**: `/status`
- **Method**: `GET`
- **Description**: Returns the current status of the Lesson Plan Manager.

### Get Current/Next Lesson
- **URL**: `/api/whatnow/<group_number>`
- **Method**: `GET`
- **Description**: Returns information about the current or next lesson for the specified group.

### Set Test Time
- **URL**: `/api/set_test_time`
- **Method**: `POST`
- **Description**: Sets a custom time for testing purposes.
- **Body**:
  ```json
  {
    "use_test_time": true,
    "test_time": "2023-05-01 14:30:00"
  }
  ```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

Made with ❤️ by Klegus