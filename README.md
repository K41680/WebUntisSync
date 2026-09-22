# WebUntis Sync — Quick Start & Troubleshooting

This project fetches your WebUntis timetable and publishes an iCalendar file (calendar.ics) via GitHub Pages. The calendar entries remain in the language your WebUntis is configured with.

## Features
* **Smart Merging**: Merges overlapping lessons (e.g., co-teaching) and adjacent hours of the same subject into single calendar events.
* **Semester Switching**: Can fetch both your current exam schedule and the upcoming semester's timetable simultaneously.
* **Efficient Scheduling**: Updates hourly during the day (European time) and less frequently at night to save GitHub Action minutes.

## How to use (Fork & Setup)

If you want to use this, you only need to fork the repo and follow these steps:

### 1. Fork this repository
* Click "Fork" in the top-right of this repository. This creates a copy under your GitHub account.

### 2. Add your WebUntis credentials as GitHub Secrets
* Go to your fork → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
* Add these secrets (exact names required):
    * `WEBUNTIS_SERVER`: Your WebUntis host (example: `school.webuntis.com`)
    * `WEBUNTIS_SCHOOL`: School identifier used in WebUntis URL
    * `WEBUNTIS_USERNAME`: Your WebUntis username
    * `WEBUNTIS_PASSWORD`: Your WebUntis password
    * `WEBUNTIS_CLASS_ID`: (Optional) The class entityId from your WebUntis URL (example: `1234`), if you want to fetch a class timetable instead of a personal one.

    **Optional Secrets for Semester Switching:**
    * `WEBUNTIS_FUTURE_CLASS_ID`: (Optional) The class ID for the *next* semester/group.
    * `SEMESTER_SWITCH_DATE`: (Optional) The date when the schedule switches to the new class (Format: `YYYY-MM-DD`).
    
    **Optional Secret to ignore classes not taken in this year:**
    * `WEBUNTIS_IGNORED_SUBJECTS`: (Optional) Sometimes you don't take all the classes in your class, you can ignore the ones you don't take by entering it's name (if it's more than one, seperate by comma).
    
    *Note: Keep these secrets private. Do NOT commit passwords into the repository.*

### 3. Allow the workflow to update the repository
* Go to your fork → **Settings** → **Actions** → **General** → **Workflow permissions**.
* Select **Read and write** permissions. This allows the automatic job to commit the `calendar.ics` file back to your repo.
* Click **Save**.

### 4. Enable GitHub Pages
* Go to your fork → **Settings** → **Pages**.
* **Source**: Select Branch `main` and Folder `/docs`.
* Click **Save**. GitHub Pages will verify the folder exists after the first successful run.

### 5. Run the workflow (First Run)
* Go to your fork → **Actions** → **Sync WebUntis Calendar**.
* Click **Run workflow**, choose `main`, and confirm.
* Wait for the workflow to finish. It should:
    * Run the Python script and generate `docs/calendar.ics`.
    * Commit and push the file.
    * Trigger a GitHub Pages deployment.

## Where to find your calendar
After a successful run, your calendar is available at:

`https://<your-github-username>.github.io/<repo-name>/calendar.ics`

* **Outlook/Google Calendar**: Use "Add calendar from Internet" and paste the URL.
* *Note: If the URL returns a 404 error immediately after the first run, wait a few minutes for GitHub Pages to deploy.*

## Troubleshooting
If the run fails:
1.  Open the **Actions** tab.
2.  Click on the failed run.
3.  Check the "Run sync script" step log. Common errors include incorrect passwords or school names.
