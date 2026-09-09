# How to Check if Backend is Functional

## Quick Start Guide

### Step 1: Activate Virtual Environment
```bash
cd ~/Downloads/car
source venv/bin/activate
```

### Step 2: Start the Django Server
```bash
python manage.py runserver
```

**Keep this terminal window open!** The server should show:
```
Starting development server at http://127.0.0.1:8000/
```

### Step 3: Test the Backend (Choose one method)

---

## Method 1: Automated Test Script (Recommended) ⭐

**In a NEW terminal window** (keep server running in the first one):

```bash
cd ~/Downloads/car
source venv/bin/activate
python3 test_api.py
```

**Expected Result:**
- ✅ All 15 tests should pass
- ✅ Success Rate: 100.0%
- ✅ No failures

---

## Method 2: Manual Health Check

**In a NEW terminal window** or browser:

```bash
curl http://localhost:8000/health/
```

**Or open in browser:** http://localhost:8000/health/

**Expected Result:**
```json
{
  "status": "OK",
  "timestamp": "2024-01-27T..."
}
```

---

## Method 3: Test Individual Endpoints

### Test User Registration
```bash
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "test123456",
    "password2": "test123456",
    "name": "Test User",
    "phone": "+1234567890"
  }'
```

### Test Get All Cars
```bash
curl http://localhost:8000/api/cars/
```

---

## Method 4: Check Server Logs

When the server is running, you should see:
- ✅ No error messages
- ✅ "GET /health/ HTTP/1.1" 200
- ✅ "GET /api/cars/ HTTP/1.1" 200

---

## Troubleshooting

### Server won't start?
1. Check if port 8000 is already in use:
   ```bash
   lsof -i :8000
   ```
2. Kill the process if needed:
   ```bash
   kill -9 <PID>
   ```

### Database errors?
```bash
python manage.py migrate
```

### Import errors?
```bash
source venv/bin/activate
pip install -r requirements.txt
```

---

## Quick Verification Checklist

- [ ] Virtual environment activated
- [ ] Server running on http://localhost:8000
- [ ] Health endpoint returns 200 OK
- [ ] Test script shows 100% pass rate
- [ ] No errors in server logs

---

## Full Test Results Should Show:

```
✅ Passed: 15
❌ Failed: 0
Total Tests: 15
Success Rate: 100.0%
```

All tests should pass:
1. ✅ Health Check
2. ✅ User Registration
3. ✅ User Login
4. ✅ Get Profile
5. ✅ Create Car Listing
6. ✅ Get All Cars
7. ✅ Search Cars
8. ✅ Get Single Car
9. ✅ Update Car
10. ✅ Get My Listings
11. ✅ Add to Favorites
12. ✅ Get Favorites
13. ✅ Check Favorite Status
14. ✅ Remove from Favorites
15. ✅ Delete Car



