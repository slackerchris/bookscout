# BookScout v2 - Update Guide

## What's New

✅ **Bulk Import from Audiobookshelf** - Import all authors from your 1800+ book library at once
✅ **Show Missing Only Filter** - View only books you don't have yet
✅ **Dark Mode** - Toggle with moon/sun icon in navbar (persists across sessions)
✅ **Success Messages** - Save button now shows confirmation and redirects to home
✅ **Better UX** - Flash messages for all actions

---

## How to Update

### Option 1: Download New Files

1. **Download updated version:**
   - [bookscout.tar.gz](computer:///mnt/user-data/outputs/bookscout.tar.gz)
   - [bookscout.zip](computer:///mnt/user-data/outputs/bookscout.zip)

2. **On your server:**
```bash
# Stop the container
docker stop bookscout

# Backup your database (important!)
cp ~/bookscout/bookscout.db ~/bookscout.db.backup

# Extract new files (overwrite old ones)
cd ~
tar -xzf bookscout.tar.gz  # or unzip bookscout.zip

# Rebuild image
cd bookscout
docker build -t bookscout:latest .

# Restart in Portainer
# Or manually: docker start bookscout
```

### Option 2: Manual File Updates (If You Prefer)

Just replace these files on your server:
- `app.py` (updated)
- `templates/base.html` (updated)
- `templates/index.html` (updated)
- `templates/author.html` (updated)
- `templates/settings.html` (updated)
- `Dockerfile` (fixed)

Then rebuild: `docker build -t bookscout:latest .`

---

## New Features Usage

### 1. Bulk Import from Audiobookshelf

**On the home page:**
1. Click **"Import from Audiobookshelf"** button
2. Confirm the import
3. Wait (may take 30-60 seconds for 1800+ books)
4. All authors from your library will be added to watchlist

**Then:**
- Click "Scan All" to find missing books for all authors
- Or scan authors individually

### 2. Show Missing Only Filter

**On any author page:**
1. Click **"Show Missing Only"** button
2. See only books you don't have yet
3. Click **"Show All Books"** to see everything again

### 3. Dark Mode

**Toggle anytime:**
- Click the moon icon (☾) in the navbar to enable dark mode
- Click the sun icon (☀) to switch back to light mode
- Your preference is saved automatically

### 4. Success Messages

**Now you'll see:**
- Green success messages when settings save
- Confirmation when authors are added
- Notifications when bulk import completes
- All messages auto-dismiss after a few seconds

---

## Important Notes

**Your database is preserved** - The update doesn't touch `bookscout.db`, so:
- ✅ Your existing authors remain
- ✅ Your settings remain
- ✅ Your scan history remains

**Bulk import will:**
- Add new authors from ABS
- Skip authors already in your watchlist
- Show you how many were added vs skipped

**After updating:**
1. Bulk import all authors from ABS
2. Click "Scan All" (this will take a while with many authors)
3. Use "Show Missing Only" to see gaps in your library

---

## Workflow for Your 1800+ Books

**First Time:**
1. Update to v2
2. Bulk import from Audiobookshelf
3. Click "Scan All" (go make coffee, this will take 10-20 minutes)
4. Browse authors and click "Show Missing Only"
5. Search for missing books via Prowlarr

**Ongoing:**
- Weekly: Click "Scan All" to find new releases
- Or: Scan specific authors you're actively following
- Use filters to focus on missing books only

---

## Troubleshooting

**Bulk import button does nothing:**
→ Check Settings - make sure Audiobookshelf URL and token are configured

**Import says "0 authors found":**
→ Verify your ABS token has the right permissions
→ Try accessing ABS API manually to test

**Dark mode doesn't persist:**
→ Your browser is blocking localStorage (check privacy settings)

**Still see old interface:**
→ Hard refresh browser (Ctrl+F5 or Cmd+Shift+R)
→ Clear browser cache

---

## Questions?

The new features are designed specifically for your use case:
- Large existing library (1800+ books)
- Need to find what's missing
- Want to monitor for new releases
- Prefer dark mode

Try the bulk import first - it's the game-changer for your workflow!
