# macOS Setup Guide - Quick Fix for "command not found: python"

## Problem
When you type `python manage.py runserver`, you get:
```
zsh: command not found: python
```

## Solution
On macOS, use `python3` instead of `python`!

---

## Quick Fix Steps

### Step 1: Use `python3` instead of `python`

**Instead of:**
```bash
python manage.py runserver
```

**Use:**
```bash
python3 manage.py runserver
```

---

## Complete Setup for macOS

### Step 1: Install Dependencies (First Time)

```bash
# Install Python packages
python3 -m pip install -r requirements.txt
```

### Step 2: Create Virtual Environment (Recommended)

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Now you can use just 'python' after activation
python manage.py runserver
```

### Step 3: Setup Database

```bash
# Create .env file first (see below)
# Then run migrations
python3 manage.py makemigrations
python3 manage.py migrate
```

### Step 4: Create Superuser (Optional)

```bash
python3 manage.py createsuperuser
```

### Step 5: Run Server

```bash
python3 manage.py runserver
```

Server will start at: `http://localhost:8000`

---

## Important: Create .env File

Before running migrations, create a `.env` file:

```bash
# In the project directory
cat > .env << EOF
SECRET_KEY=test-secret-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=car_marketplace
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
EOF
```

**Or create it manually:**
1. Create a file named `.env` in the project root
2. Add the content above

---

## All Commands with `python3`

```bash
# Install dependencies
python3 -m pip install -r requirements.txt

# Run migrations
python3 manage.py makemigrations
python3 manage.py migrate

# Create superuser
python3 manage.py createsuperuser

# Run server
python3 manage.py runserver

# Run tests
python3 test_api.py
```

---

## Alternative: Use Virtual Environment

**Once you activate virtual environment, you can use `python`:**

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Now you'll see (venv) in your terminal
# Now you can use 'python' directly:
python manage.py runserver
```

**To deactivate:**
```bash
deactivate
```

---

## Troubleshooting

### Issue 1: "pip not found"
```bash
python3 -m pip install -r requirements.txt
```

### Issue 2: "Django not found"
```bash
python3 -m pip install Django djangorestframework
```

### Issue 3: "Database connection error"
- Make sure PostgreSQL is installed and running
- Check your `.env` file has correct database credentials

### Issue 4: "Port already in use"
```bash
# Use different port
python3 manage.py runserver 8001
```

---

## Quick Reference

| Command | What It Does |
|---------|-------------|
| `python3 --version` | Check Python version |
| `python3 -m pip install -r requirements.txt` | Install packages |
| `python3 manage.py runserver` | Start server |
| `python3 manage.py migrate` | Setup database |
| `python3 manage.py createsuperuser` | Create admin user |
| `python3 test_api.py` | Run tests |

---

## Summary

**Always use `python3` on macOS instead of `python`!**

```bash
python3 manage.py runserver  ✅
python manage.py runserver   ❌ (won't work)
```



