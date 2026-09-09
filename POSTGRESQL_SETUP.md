# PostgreSQL Setup Guide for macOS

## Current Status: ❌ PostgreSQL NOT Installed

PostgreSQL is required for your Django backend to work. Let's install it!

---

## Step 1: Check if Homebrew is Installed

```bash
brew --version
```

**If you see a version number** → Homebrew is installed ✅  
**If you see "command not found"** → Need to install Homebrew first (see below)

---

## Step 2A: Install Homebrew (If Not Installed)

Homebrew is the package manager for macOS. Install it first:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

This will:
- Download Homebrew
- Install it to `/opt/homebrew` (on Apple Silicon) or `/usr/local` (on Intel)
- Add Homebrew to your PATH

**Follow the prompts** and enter your password when asked.

---

## Step 2B: Install PostgreSQL via Homebrew

Once Homebrew is installed:

```bash
# Install PostgreSQL
brew install postgresql@15

# Or install latest version:
brew install postgresql
```

**Wait for installation** - this may take a few minutes.

---

## Step 3: Start PostgreSQL Service

After installation:

```bash
# Start PostgreSQL (runs automatically on startup)
brew services start postgresql@15

# Or if you installed latest version:
brew services start postgresql
```

**To check if it's running:**
```bash
brew services list
```

You should see `postgresql@15` (or `postgresql`) with status `started` ✅

---

## Step 4: Verify Installation

```bash
# Check PostgreSQL version
psql --version

# Connect to PostgreSQL
psql postgres
```

**If you see PostgreSQL prompt**, you're good! Type `\q` to exit.

---

## Step 5: Create Database for Your Project

```bash
# Connect to PostgreSQL
psql postgres

# In PostgreSQL prompt, create database:
CREATE DATABASE car_marketplace;

# Create user (optional, or use postgres user)
CREATE USER caruser WITH PASSWORD 'carpassword';

# Grant privileges
GRANT ALL PRIVILEGES ON DATABASE car_marketplace TO caruser;

# Exit
\q
```

---

## Step 6: Update Your .env File

Make sure your `.env` file has correct database settings:

```env
DB_NAME=car_marketplace
DB_USER=postgres
DB_PASSWORD=your_postgres_password
DB_HOST=localhost
DB_PORT=5432
```

**Note:** If you created a user above, use those credentials. Otherwise, use `postgres` as user.

---

## Step 7: Test Django Connection

```bash
# Run migrations to test database connection
python3 manage.py migrate
```

**If you see migrations running successfully** → Database is connected! ✅

---

## Quick Installation Commands (All in One)

```bash
# 1. Install Homebrew (if needed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Install PostgreSQL
brew install postgresql@15

# 3. Start PostgreSQL
brew services start postgresql@15

# 4. Verify
psql --version
```

---

## Troubleshooting

### Issue 1: "command not found: brew"

**Solution:** Homebrew is not installed. Install it using the command in Step 2A.

---

### Issue 2: "Permission denied"

**Solution:** Make sure Homebrew directory is in your PATH:

```bash
# For Apple Silicon Macs:
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# For Intel Macs:
echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/usr/local/bin/brew shellenv)"
```

---

### Issue 3: "PostgreSQL connection refused"

**Solution:** Make sure PostgreSQL is running:

```bash
# Check status
brew services list

# If not started, start it:
brew services start postgresql@15

# Or restart if needed:
brew services restart postgresql@15
```

---

### Issue 4: "database does not exist"

**Solution:** Create the database:

```bash
psql postgres -c "CREATE DATABASE car_marketplace;"
```

---

### Issue 5: "password authentication failed"

**Solution:** 
- Check your `.env` file has correct password
- Default postgres user might not have a password set
- Create a new user with password (see Step 5)

---

## Alternative: Use SQLite (For Testing Only)

If you just want to test without installing PostgreSQL, you can use SQLite temporarily:

**In `car_marketplace/settings.py`, change:**

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```

**Note:** SQLite is fine for development/testing, but PostgreSQL is recommended for production.

---

## Check PostgreSQL Installation

Run these commands to verify:

```bash
# Check if PostgreSQL is installed
psql --version

# Check if service is running
brew services list | grep postgres

# Test connection
psql postgres -c "SELECT version();"
```

**If all commands work** → PostgreSQL is ready! ✅

---

## Summary

**Current Status:** ❌ PostgreSQL NOT installed

**What to do:**
1. Install Homebrew (if not installed)
2. Install PostgreSQL: `brew install postgresql@15`
3. Start PostgreSQL: `brew services start postgresql@15`
4. Create database: `CREATE DATABASE car_marketplace;`
5. Update `.env` file with database credentials
6. Run migrations: `python3 manage.py migrate`

**After setup** → Your backend can connect to database! 🚀



