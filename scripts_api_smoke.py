#!/usr/bin/env python3
"""
Automated API Testing Script
Run this to test all endpoints automatically
"""

import requests
import json
import os
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
TEST_EMAIL = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}@example.com"
TEST_PASSWORD = "test123456"

# Test results
results = {
    "passed": 0,
    "failed": 0,
    "tests": []
}

def log_test(name, status, message=""):
    """Log test result"""
    result = {
        "test": name,
        "status": status,
        "message": message
    }
    results["tests"].append(result)
    if status == "PASS":
        results["passed"] += 1
        print(f"✅ PASS: {name}")
    else:
        results["failed"] += 1
        print(f"❌ FAIL: {name} - {message}")
    return status == "PASS"

# Global variables to store data between tests
access_token = None
user_id = None
car_id = None
image_id = None

print("=" * 60)
print("CAR MARKETPLACE API TESTING")
print("=" * 60)
print(f"\nTesting against: {BASE_URL}\n")

# Test 1: Health Check
print("\n1. Testing Health Check...")
try:
    response = requests.get(f"{BASE_URL}/health/")
    if response.status_code == 200:
        data = response.json()
        log_test("Health Check", "PASS", f"Server is running - {data.get('status')}")
    else:
        log_test("Health Check", "FAIL", f"Status code: {response.status_code}")
except Exception as e:
    log_test("Health Check", "FAIL", str(e))

# Test 2: User Registration
print("\n2. Testing User Registration...")
try:
    payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
        "password2": TEST_PASSWORD,
        "name": "Test User",
        "phone": "+1234567890"
    }
    response = requests.post(f"{BASE_URL}/api/auth/register/", json=payload)
    if response.status_code == 201:
        data = response.json()
        access_token = data.get("tokens", {}).get("access")
        user_id = data.get("user", {}).get("id")
        log_test("User Registration", "PASS", f"User ID: {user_id}")
    else:
        log_test("User Registration", "FAIL", f"Status: {response.status_code}, {response.text}")
except Exception as e:
    log_test("User Registration", "FAIL", str(e))

# Test 3: User Login
print("\n3. Testing User Login...")
try:
    payload = {
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD
    }
    response = requests.post(f"{BASE_URL}/api/auth/login/", json=payload)
    if response.status_code == 200:
        data = response.json()
        access_token = data.get("tokens", {}).get("access")
        log_test("User Login", "PASS", "Login successful")
    else:
        log_test("User Login", "FAIL", f"Status: {response.status_code}")
except Exception as e:
    log_test("User Login", "FAIL", str(e))

# Test 4: Get Profile
print("\n4. Testing Get Profile...")
try:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{BASE_URL}/api/auth/me/", headers=headers)
    if response.status_code == 200:
        log_test("Get Profile", "PASS", "Profile retrieved")
    else:
        log_test("Get Profile", "FAIL", f"Status: {response.status_code}")
except Exception as e:
    log_test("Get Profile", "FAIL", str(e))

# Test 5: Create Car Listing
print("\n5. Testing Create Car Listing...")
try:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "title": "2020 Toyota Camry Test",
        "description": "Test car listing",
        "make": "Toyota",
        "model": "Camry",
        "year": 2020,
        "price": "25000.00",
        "mileage": 30000,
        "color": "White",
        "fuel_type": "PETROL",
        "transmission": "AUTOMATIC",
        "condition": "USED",
        "location": "Test City"
    }
    response = requests.post(f"{BASE_URL}/api/cars/", json=payload, headers=headers)
    if response.status_code == 201:
        data = response.json()
        car_id = data.get("id")
        log_test("Create Car Listing", "PASS", f"Car ID: {car_id}")
    else:
        log_test("Create Car Listing", "FAIL", f"Status: {response.status_code}, {response.text}")
except Exception as e:
    log_test("Create Car Listing", "FAIL", str(e))

# Test 6: Get All Cars
print("\n6. Testing Get All Cars...")
try:
    response = requests.get(f"{BASE_URL}/api/cars/")
    if response.status_code == 200:
        data = response.json()
        count = len(data.get("results", []))
        log_test("Get All Cars", "PASS", f"Found {count} cars")
    else:
        log_test("Get All Cars", "FAIL", f"Status: {response.status_code}")
except Exception as e:
    log_test("Get All Cars", "FAIL", str(e))

# Test 7: Search Cars
print("\n7. Testing Search Cars...")
try:
    response = requests.get(f"{BASE_URL}/api/cars/?search=Toyota")
    if response.status_code == 200:
        log_test("Search Cars", "PASS", "Search working")
    else:
        log_test("Search Cars", "FAIL", f"Status: {response.status_code}")
except Exception as e:
    log_test("Search Cars", "FAIL", str(e))

# Test 8: Get Single Car
print("\n8. Testing Get Single Car...")
try:
    if car_id:
        response = requests.get(f"{BASE_URL}/api/cars/{car_id}/")
        if response.status_code == 200:
            log_test("Get Single Car", "PASS", "Car retrieved")
        else:
            log_test("Get Single Car", "FAIL", f"Status: {response.status_code}")
    else:
        log_test("Get Single Car", "SKIP", "No car ID available")
except Exception as e:
    log_test("Get Single Car", "FAIL", str(e))

# Test 9: Update Car
print("\n9. Testing Update Car...")
try:
    if car_id:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {"price": "24000.00"}
        response = requests.patch(f"{BASE_URL}/api/cars/{car_id}/", json=payload, headers=headers)
        if response.status_code == 200:
            log_test("Update Car", "PASS", "Car updated")
        else:
            log_test("Update Car", "FAIL", f"Status: {response.status_code}")
    else:
        log_test("Update Car", "SKIP", "No car ID available")
except Exception as e:
    log_test("Update Car", "FAIL", str(e))

# Test 10: Get My Listings
print("\n10. Testing Get My Listings...")
try:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{BASE_URL}/api/cars/my/listings/", headers=headers)
    if response.status_code == 200:
        log_test("Get My Listings", "PASS", "Listings retrieved")
    else:
        log_test("Get My Listings", "FAIL", f"Status: {response.status_code}")
except Exception as e:
    log_test("Get My Listings", "FAIL", str(e))

# Test 11: Add to Favorites
print("\n11. Testing Add to Favorites...")
try:
    if car_id:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.post(f"{BASE_URL}/api/favorites/car/{car_id}/", headers=headers)
        if response.status_code == 201:
            log_test("Add to Favorites", "PASS", "Added to favorites")
        else:
            log_test("Add to Favorites", "FAIL", f"Status: {response.status_code}, {response.text}")
    else:
        log_test("Add to Favorites", "SKIP", "No car ID available")
except Exception as e:
    log_test("Add to Favorites", "FAIL", str(e))

# Test 12: Get Favorites
print("\n12. Testing Get Favorites...")
try:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{BASE_URL}/api/favorites/", headers=headers)
    if response.status_code == 200:
        log_test("Get Favorites", "PASS", "Favorites retrieved")
    else:
        log_test("Get Favorites", "FAIL", f"Status: {response.status_code}")
except Exception as e:
    log_test("Get Favorites", "FAIL", str(e))

# Test 13: Check Favorite Status
print("\n13. Testing Check Favorite Status...")
try:
    if car_id:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(f"{BASE_URL}/api/favorites/car/{car_id}/check/", headers=headers)
        if response.status_code == 200:
            log_test("Check Favorite Status", "PASS", "Status checked")
        else:
            log_test("Check Favorite Status", "FAIL", f"Status: {response.status_code}")
    else:
        log_test("Check Favorite Status", "SKIP", "No car ID available")
except Exception as e:
    log_test("Check Favorite Status", "FAIL", str(e))

# Test 14: Remove from Favorites
print("\n14. Testing Remove from Favorites...")
try:
    if car_id:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.delete(f"{BASE_URL}/api/favorites/car/{car_id}/", headers=headers)
        if response.status_code in [200, 204]:
            log_test("Remove from Favorites", "PASS", "Removed from favorites")
        else:
            log_test("Remove from Favorites", "FAIL", f"Status: {response.status_code}")
    else:
        log_test("Remove from Favorites", "SKIP", "No car ID available")
except Exception as e:
    log_test("Remove from Favorites", "FAIL", str(e))

# Test 15: Delete Car
print("\n15. Testing Delete Car...")
try:
    if car_id:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.delete(f"{BASE_URL}/api/cars/{car_id}/", headers=headers)
        if response.status_code in [200, 204]:
            log_test("Delete Car", "PASS", "Car deleted")
        else:
            log_test("Delete Car", "FAIL", f"Status: {response.status_code}")
    else:
        log_test("Delete Car", "SKIP", "No car ID available")
except Exception as e:
    log_test("Delete Car", "FAIL", str(e))

# Print Summary
print("\n" + "=" * 60)
print("TEST SUMMARY")
print("=" * 60)
print(f"✅ Passed: {results['passed']}")
print(f"❌ Failed: {results['failed']}")
print(f"Total Tests: {len(results['tests'])}")
print(f"Success Rate: {(results['passed'] / len(results['tests']) * 100):.1f}%")
print("=" * 60)

# Print failed tests
if results['failed'] > 0:
    print("\nFailed Tests:")
    for test in results['tests']:
        if test['status'] == 'FAIL':
            print(f"  ❌ {test['test']}: {test['message']}")

# Exit with appropriate code
exit(0 if results['failed'] == 0 else 1)



