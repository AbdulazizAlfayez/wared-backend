# Quick Start - Testing Your Backend

## 🚀 Fast Testing Guide (3 Steps)

### Step 1: Setup (First Time Only)

```bash
# Install Python packages
pip install -r requirements.txt

# Create .env file (copy from .env.example or create manually)
# Then run migrations
python manage.py migrate
```

### Step 2: Start Server

```bash
python manage.py runserver
```

Server runs at: `http://localhost:8000`

### Step 3: Run Automatic Tests

**Option A: Automatic Testing (Easy)**
```bash
# In a new terminal (keep server running)
python test_api.py
```

This will test everything automatically! ✅

**Option B: Manual Testing (Step by Step)**

Open browser and test:

1. **Health Check:** `http://localhost:8000/health/`
   - Should show: `{"status": "OK", ...}`

2. **View Cars:** `http://localhost:8000/api/cars/`
   - Should show empty list: `{"count": 0, "results": []}`

3. **Django Admin:** `http://localhost:8000/admin/`
   - Create superuser first: `python manage.py createsuperuser`

---

## 📝 Manual Testing with Postman/Thunder Client

1. **Download Postman** or use **Thunder Client** (VS Code extension)

2. **Test Registration:**
   - URL: `POST http://localhost:8000/api/auth/register/`
   - Body (JSON):
     ```json
     {
       "email": "test@example.com",
       "password": "test123456",
       "password2": "test123456",
       "name": "Test User"
     }
     ```
   - ✅ Should return tokens

3. **Save the Access Token** from response

4. **Test Create Car:**
   - URL: `POST http://localhost:8000/api/cars/`
   - Headers: `Authorization: Bearer YOUR_TOKEN_HERE`
   - Body (JSON):
     ```json
     {
       "title": "2020 Toyota Camry",
       "make": "Toyota",
       "model": "Camry",
       "year": 2020,
       "price": "25000.00",
       "mileage": 30000,
       "fuel_type": "PETROL",
       "transmission": "AUTOMATIC",
       "condition": "USED",
       "location": "New York"
     }
     ```

5. **Test View Cars:**
   - URL: `GET http://localhost:8000/api/cars/`
   - ✅ Should see your car

---

## ✅ What Each Test Checks

| Test | What It Checks |
|------|----------------|
| Health Check | Server is running |
| Registration | Can create new user |
| Login | Can authenticate user |
| Profile | Can get user info |
| Create Car | Can add car listing |
| List Cars | Can view all cars |
| Search | Can search cars |
| Update Car | Can modify listing |
| Delete Car | Can remove listing |
| Upload Image | Can add photos |
| Add Favorite | Can save cars |
| View Favorites | Can see saved cars |

---

## 🎯 Success Indicators

**Your backend is fully functional if:**

- ✅ Server starts without errors
- ✅ Health endpoint returns OK
- ✅ Can register/login users
- ✅ Can create/view cars
- ✅ Can search/filter cars
- ✅ Can upload images
- ✅ Can manage favorites

---

## 🔧 Troubleshooting

**"Module not found" error?**
```bash
pip install -r requirements.txt
```

**"Database error"?**
```bash
python manage.py migrate
```

**"Port already in use"?**
```bash
# Use different port
python manage.py runserver 8001
```

**"CORS error" in browser?**
- Make sure `django-cors-headers` is installed
- Check settings.py has CORS configured

---

## 📚 Full Testing Guide

For detailed step-by-step testing, see: **TESTING_GUIDE.md**



