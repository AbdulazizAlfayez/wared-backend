# Testing Guide - How to Test Your Car Marketplace Backend

This guide will help you test if everything is working correctly.

## Step 1: Setup and Installation

### 1.1 Install Dependencies
```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 1.2 Setup Database

First, make sure PostgreSQL is running on your system.

Create a `.env` file:
```env
SECRET_KEY=test-secret-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=car_marketplace
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
```

Then run migrations:
```bash
python manage.py makemigrations
python manage.py migrate
```

### 1.3 Create Superuser (Optional)
```bash
python manage.py createsuperuser
# Follow prompts to create admin user
```

### 1.4 Start the Server
```bash
python manage.py runserver
```

Server will start at: `http://localhost:8000`

---

## Step 2: Manual Testing with Browser/Postman

### Test 1: Health Check ✅

**URL:** `http://localhost:8000/health/`

**Method:** GET

**Expected Result:** 
```json
{
  "status": "OK",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

**✅ If you see this, your server is running!**

---

### Test 2: User Registration ✅

**URL:** `http://localhost:8000/api/auth/register/`

**Method:** POST

**Headers:**
```
Content-Type: application/json
```

**Body:**
```json
{
  "email": "test@example.com",
  "password": "test123456",
  "password2": "test123456",
  "name": "Test User",
  "phone": "+1234567890"
}
```

**Expected Result:** Status 201 Created
```json
{
  "message": "User registered successfully",
  "user": {
    "id": 1,
    "email": "test@example.com",
    "name": "Test User",
    "phone": "+1234567890",
    "role": "SELLER"
  },
  "tokens": {
    "refresh": "...",
    "access": "..."
  }
}
```

**✅ If you see this, registration works!**

**📝 SAVE THE ACCESS TOKEN** - You'll need it for protected endpoints!

---

### Test 3: User Login ✅

**URL:** `http://localhost:8000/api/auth/login/`

**Method:** POST

**Body:**
```json
{
  "email": "test@example.com",
  "password": "test123456"
}
```

**Expected Result:** Status 200 OK
```json
{
  "message": "Login successful",
  "user": { ... },
  "tokens": {
    "refresh": "...",
    "access": "..."
  }
}
```

**✅ If you see this, login works!**

**📝 SAVE THE ACCESS TOKEN**

---

### Test 4: Get User Profile ✅

**URL:** `http://localhost:8000/api/auth/me/`

**Method:** GET

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:** Status 200 OK
```json
{
  "user": {
    "id": 1,
    "email": "test@example.com",
    "name": "Test User",
    ...
  }
}
```

**✅ If you see this, authentication works!**

---

### Test 5: Create Car Listing ✅

**URL:** `http://localhost:8000/api/cars/`

**Method:** POST

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
Content-Type: application/json
```

**Body:**
```json
{
  "title": "2020 Toyota Camry",
  "description": "Well maintained car, single owner",
  "make": "Toyota",
  "model": "Camry",
  "year": 2020,
  "price": "25000.00",
  "mileage": 30000,
  "color": "White",
  "fuel_type": "PETROL",
  "transmission": "AUTOMATIC",
  "condition": "USED",
  "location": "New York, NY"
}
```

**Expected Result:** Status 201 Created
```json
{
  "id": 1,
  "title": "2020 Toyota Camry",
  "make": "Toyota",
  ...
}
```

**✅ If you see this, car creation works!**

**📝 SAVE THE CAR ID** - You'll need it for images

---

### Test 6: Get All Cars ✅

**URL:** `http://localhost:8000/api/cars/`

**Method:** GET

**Expected Result:** Status 200 OK
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "title": "2020 Toyota Camry",
      ...
    }
  ]
}
```

**✅ If you see your car, listing works!**

---

### Test 7: Search Cars ✅

**URL:** `http://localhost:8000/api/cars/?search=Toyota`

**Method:** GET

**Expected Result:** Should return cars matching "Toyota"

**Try other searches:**
- `?make=Toyota`
- `?min_price=20000&max_price=30000`
- `?year=2020`
- `?fuel_type=PETROL`

**✅ If filters work, search is working!**

---

### Test 8: Get Single Car ✅

**URL:** `http://localhost:8000/api/cars/1/`

**Method:** GET

**Expected Result:** Full car details with all information

**✅ If you see full details, single car view works!**

---

### Test 9: Update Car ✅

**URL:** `http://localhost:8000/api/cars/1/`

**Method:** PUT or PATCH

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Body:**
```json
{
  "title": "2020 Toyota Camry - Updated Price",
  "price": "24000.00"
}
```

**Expected Result:** Status 200 OK with updated data

**✅ If price updated, update works!**

---

### Test 10: Upload Car Image ✅

**URL:** `http://localhost:8000/api/cars/images/`

**Method:** POST

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Body (Form-Data):**
```
car_id: 1
image: [Select a JPG/PNG file]
```

**Expected Result:** Status 201 Created
```json
{
  "id": 1,
  "image": "/media/cars/filename.jpg",
  ...
}
```

**✅ If image uploaded, image upload works!**

---

### Test 11: Add to Favorites ✅

**URL:** `http://localhost:8000/api/favorites/car/1/`

**Method:** POST

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:** Status 201 Created
```json
{
  "message": "Added to favorites",
  "favorite": { ... }
}
```

**✅ If added, favorites work!**

---

### Test 12: Get Favorites ✅

**URL:** `http://localhost:8000/api/favorites/`

**Method:** GET

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:** List of your favorite cars

**✅ If you see your favorites, favorites list works!**

---

### Test 13: Check Favorite Status ✅

**URL:** `http://localhost:8000/api/favorites/car/1/check/`

**Method:** GET

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:**
```json
{
  "is_favorite": true
}
```

**✅ If status is correct, check favorite works!**

---

### Test 14: Remove from Favorites ✅

**URL:** `http://localhost:8000/api/favorites/car/1/`

**Method:** DELETE

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:** Status 200 OK
```json
{
  "message": "Removed from favorites"
}
```

**✅ If removed, delete favorite works!**

---

### Test 15: Get My Listings ✅

**URL:** `http://localhost:8000/api/cars/my/listings/`

**Method:** GET

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:** List of cars you created

**✅ If you see your cars, my listings works!**

---

### Test 16: Delete Car ✅

**URL:** `http://localhost:8000/api/cars/1/`

**Method:** DELETE

**Headers:**
```
Authorization: Bearer YOUR_ACCESS_TOKEN_HERE
```

**Expected Result:** Status 204 No Content (or 200 OK)

**✅ If deleted, delete car works!**

---

## Step 3: Using Automated Test Script

I'll create a Python test script for you that tests everything automatically.

## Common Issues & Solutions

### Issue 1: "ModuleNotFoundError: No module named 'django'"
**Solution:** Install dependencies: `pip install -r requirements.txt`

### Issue 2: "Could not connect to database"
**Solution:** Check PostgreSQL is running and `.env` file has correct database credentials

### Issue 3: "401 Unauthorized"
**Solution:** Make sure you're sending the correct Authorization header with Bearer token

### Issue 4: "403 Forbidden"
**Solution:** You don't have permission. Make sure you own the resource or are an admin

### Issue 5: Images not uploading
**Solution:** Make sure you're using Form-Data (not JSON) and the file is an image

---

## Quick Test Checklist

- [ ] Server starts without errors
- [ ] Health check returns OK
- [ ] Can register new user
- [ ] Can login with credentials
- [ ] Can get own profile
- [ ] Can create car listing
- [ ] Can view all cars
- [ ] Can search/filter cars
- [ ] Can view single car
- [ ] Can update own car
- [ ] Can upload car image
- [ ] Can add car to favorites
- [ ] Can view favorites
- [ ] Can check favorite status
- [ ] Can remove from favorites
- [ ] Can view own listings
- [ ] Can delete own car

**If all checked ✅, your backend is fully functional!**



