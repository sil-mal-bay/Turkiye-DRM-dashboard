# Setup Instructions for Silvia
## Türkiye DRM Dashboard

---

### Step 1: Make the repo private (if not already)
- Go to https://github.com/sil-mal-bay/Turkiye-DRM-dashboard
- **Settings** (top menu, far right) → scroll to bottom → **Danger Zone** → "Change repository visibility" → Private

### Step 2: Add the API keys
- Go to **Settings** → left sidebar: **Secrets and variables** → **Actions**
- Click **"New repository secret"**
  - Name: `ANTHROPIC_API_KEY`
  - Value: the Claude API key (see API_KEYS_RECORD.md on Desktop)
  - Click "Add secret"
- Click **"New repository secret"** again
  - Name: `YOUTUBE_API_KEY`
  - Value: the YouTube API key (see API_KEYS_RECORD.md on Desktop)
  - Click "Add secret"

### Step 3: Enable GitHub Pages
- Go to **Settings** → left sidebar: **Pages**
- Under "Source" select **Deploy from a branch**
- Branch: **main** / folder: **/ (root)**
- Click **Save**
- After a minute or two, it will show your dashboard URL (something like `https://sil-mal-bay.github.io/Turkiye-DRM-dashboard/`)

### Step 4: Run the pipeline manually (first time)
- Go to the **Actions** tab at the top of the repo
- You may need to click "I understand my workflows, go ahead and enable them" if prompted
- You will see **"Update Dashboard Data"** on the left sidebar
- Click it → click **"Run workflow"** dropdown → click the green **"Run workflow"** button
- Wait a few minutes — it fetches all the data and commits the JSON files

### Step 5: Verify
- Check the **Actions** tab — the run should show a green checkmark
- Go to your GitHub Pages URL — the dashboard should show live data
- Going forward, it runs automatically every day at 7:30 AM Turkey time

---

### How to trigger a manual refresh
If something big happens and you want fresh data immediately:
1. Go to the repo → **Actions** tab
2. Click **"Update Dashboard Data"** → **"Run workflow"** → **"Run workflow"**
3. Dashboard updates in a few minutes

---

### Troubleshooting
- **Red X on Actions run:** Click into the failed run to see the error log. Most likely cause: API key not set correctly in Secrets.
- **Dashboard shows old data:** Check that the Action ran successfully. Try a manual trigger.
- **"Pages not found":** Make sure GitHub Pages is enabled (Step 3) and the branch is set to main.
