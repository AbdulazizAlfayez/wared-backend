# Simple Step-by-Step Guide - Getting Started 🚀

## What We've Done So Far

1. ✅ **Built your backend** - All the code files are ready
2. ✅ **Installed Python packages** - Django and other tools
3. ✅ **Installed PostgreSQL** - Your database is ready

## What You Need to Do Now (Just 4 Steps!)

### Step 1: Create Database ✅ (30 seconds)

Open terminal and run:

```bash
# Add PostgreSQL to your PATH (needed once)
export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"

# Create the database
psql postgres -c "CREATE DATABASE car_marketplace;"
```

**What this does:** Creates an empty database for your project.

---

### Step 2: Create .env File ✅ (1 minute)

Create a file named `.env` in your project folder with this content:

```
SECRET_KEY=test-secret-key-change-this-later
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=car_marketplace
DB_USER=postgres
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=5432
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
```

**What this does:** Tells Django how to connect to your database.

---

### Step 3: Setup Database Tables ✅ (1 minute)

```bash
python3 manage.py makemigrations
python3 manage.py migrate
```

**What this does:** Creates all the tables (Users, Cars, etc.) in your database.

---

### Step 4: Start Your Server ✅ (1 minute)

```bash
python3 manage.py runserver
```

**What this does:** Starts your backend server!

You should see:
```
Starting development server at http://127.0.0.1:8000/
```

---

## Test if It Works! 🎉

1. **Open your web browser**
2. **Visit:** `http://localhost:8000/api/cars/`
3. **You should see:** A webpage with `{"count": 0, "results": []}`

**If you see this → ✅ Your backend is working!**

---

## If Something Goes Wrong

### Error: "Database does not exist"
**Solution:** Make sure you did Step 1 (create database)

### Error: "Module not found"
**Solution:** Run `python3 -m pip install -r requirements.txt`

### Error: "Connection refused" or "Can't connect to database"
**Solution:** Make sure PostgreSQL is running:
```bash
brew services start postgresql@15
```

### Error: "psql: command not found"
**Solution:** Add to your terminal:
```bash
export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"
```

---

## Summary - Just Do These 4 Steps:

1. Create database
2. Create .env file
3. Run migrations
4. Start server

**That's it! Then open browser and test!**

---

## Need Help?

If you get stuck at any step, tell me which step and what error you see, and I'll help you fix it! 😊



