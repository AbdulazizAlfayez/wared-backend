# Car Marketplace Backend API

A comprehensive backend API for a car marketplace mobile/website application built with **Django** and **Django REST Framework**.

## Features

- 🔐 **Authentication & Authorization**
  - User registration and login
  - JWT-based authentication
  - Role-based access control (Buyer, Seller, Admin)

- 🚗 **Car Listings Management**
  - Create, read, update, and delete car listings
  - Advanced search and filtering
  - Pagination support

- 📸 **Image Management**
  - Upload multiple images per car
  - Delete images
  - Image validation

- ⭐ **Favorites System**
  - Add/remove cars from favorites
  - View favorite listings
  - Check favorite status

- 🔍 **Search & Filter**
  - Search by keywords (title, description, make, model)
  - Filter by make, model, price range, year, fuel type, transmission, condition, location
  - Sort and paginate results

## Tech Stack

- **Framework**: Django 5.0
- **API Framework**: Django REST Framework 3.14
- **Language**: Python 3.10+
- **Database**: PostgreSQL
- **ORM**: Django ORM
- **Authentication**: JWT (djangorestframework-simplejwt)
- **File Upload**: Django FileField with Pillow
- **Filtering**: django-filter

## Prerequisites

- Python 3.10 or higher
- PostgreSQL (v14 or higher)
- pip (Python package manager)

## Installation

1. **Create a virtual environment** (recommended)
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables**
   
   Create a `.env` file in the project root:
   ```env
   SECRET_KEY=your-django-secret-key-here
   DEBUG=True
   DB_NAME=car_marketplace
   DB_USER=postgres
   DB_PASSWORD=postgres
   DB_HOST=localhost
   DB_PORT=5432
   CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
   ```

4. **Run database migrations**
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

5. **Create a superuser** (optional, for admin access)
   ```bash
   python manage.py createsuperuser
   ```

6. **Start the development server**
   ```bash
   python manage.py runserver
   ```
   
   The server will start on `http://localhost:8000`

## Project Structure

```
car/
├── car_marketplace/      # Main project directory
│   ├── settings.py       # Django settings
│   ├── urls.py          # Main URL configuration
│   ├── wsgi.py          # WSGI configuration
│   └── asgi.py          # ASGI configuration
├── accounts/             # User authentication app
│   ├── models.py        # Custom User model
│   ├── views.py         # Authentication views
│   ├── serializers.py   # User serializers
│   └── urls.py          # Auth URLs
├── cars/                 # Car listings app
│   ├── models.py        # Car and CarImage models
│   ├── views.py         # Car viewsets
│   ├── serializers.py   # Car serializers
│   ├── filters.py       # Car filtering
│   └── urls.py          # Car URLs
├── favorites/            # Favorites app
│   ├── models.py        # Favorite model
│   ├── views.py         # Favorite viewsets
│   ├── serializers.py   # Favorite serializers
│   └── urls.py          # Favorite URLs
├── manage.py            # Django management script
└── requirements.txt     # Python dependencies
```

## API Endpoints

### Authentication

- `POST /api/auth/register/` - Register a new user
- `POST /api/auth/login/` - Login user
- `GET /api/auth/me/` - Get current user profile (Protected)
- `POST /api/auth/token/refresh/` - Refresh JWT token

### Cars

- `GET /api/cars/` - Get all cars (with filters and pagination)
- `GET /api/cars/{id}/` - Get car by ID
- `POST /api/cars/` - Create a new car listing (Protected)
- `PUT /api/cars/{id}/` - Update car listing (Protected)
- `PATCH /api/cars/{id}/` - Partially update car listing (Protected)
- `DELETE /api/cars/{id}/` - Delete car listing (Protected)
- `GET /api/cars/my/listings/` - Get current user's car listings (Protected)

### Images

- `GET /api/cars/images/` - Get all images (with optional car_id filter)
- `GET /api/cars/images/{id}/` - Get image by ID
- `POST /api/cars/images/` - Upload car image (Protected)
- `DELETE /api/cars/images/{id}/` - Delete car image (Protected)

### Favorites

- `GET /api/favorites/` - Get user's favorite cars (Protected)
- `POST /api/favorites/car/{car_id}/` - Add car to favorites (Protected)
- `DELETE /api/favorites/car/{car_id}/` - Remove car from favorites (Protected)
- `GET /api/favorites/car/{car_id}/check/` - Check if car is favorited (Protected)

## Example API Usage

### Register a User

```bash
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "seller@example.com",
    "password": "password123",
    "password2": "password123",
    "name": "John Doe",
    "phone": "+1234567890"
  }'
```

### Login

```bash
curl -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{
    "email": "seller@example.com",
    "password": "password123"
  }'
```

### Create a Car Listing

```bash
curl -X POST http://localhost:8000/api/cars/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "title": "2020 Toyota Camry",
    "description": "Well maintained car",
    "make": "Toyota",
    "model": "Camry",
    "year": 2020,
    "price": "25000.00",
    "mileage": 30000,
    "color": "White",
    "fuel_type": "PETROL",
    "transmission": "AUTOMATIC",
    "condition": "USED",
    "location": "New York"
  }'
```

### Search Cars

```bash
curl "http://localhost:8000/api/cars/?make=Toyota&min_price=20000&max_price=30000&year=2020&page=1"
```

### Upload Car Image

```bash
curl -X POST http://localhost:8000/api/cars/images/ \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -F "car_id=1" \
  -F "image=@/path/to/image.jpg"
```

## Query Parameters

### Car Listing Filters

- `make` - Filter by car make (case-insensitive)
- `model` - Filter by car model (case-insensitive)
- `min_price` - Minimum price
- `max_price` - Maximum price
- `min_year` - Minimum year
- `max_year` - Maximum year
- `fuel_type` - Filter by fuel type (PETROL, DIESEL, ELECTRIC, HYBRID, CNG, LPG)
- `transmission` - Filter by transmission (MANUAL, AUTOMATIC, CVT)
- `condition` - Filter by condition (NEW, USED, CERTIFIED_PRE_OWNED)
- `location` - Filter by location (case-insensitive)
- `status` - Filter by status (AVAILABLE, SOLD, PENDING, DRAFT)
- `search` - Search across title, description, make, and model
- `ordering` - Order by field (price, year, created_at, mileage)
- `page` - Page number for pagination
- `page_size` - Number of items per page (default: 20)

## Database Models

- **User**: Custom user model with email authentication and roles
- **Car**: Car listings with detailed information
- **CarImage**: Images associated with cars
- **Favorite**: User's favorite cars

## Django Admin

Access the Django admin panel at `http://localhost:8000/admin/`

You can manage users, cars, images, and favorites through the admin interface.

## Development

### Running Tests

```bash
python manage.py test
```

### Creating Migrations

```bash
python manage.py makemigrations
```

### Applying Migrations

```bash
python manage.py migrate
```

### Accessing Django Shell

```bash
python manage.py shell
```

### Collecting Static Files (for production)

```bash
python manage.py collectstatic
```

## Security Features

- Password hashing with Django's built-in password validators
- JWT token-based authentication
- CSRF protection
- CORS configuration
- Input validation
- File upload validation
- Role-based access control

## Production Deployment

For production deployment:

1. Set `DEBUG=False` in settings
2. Set a strong `SECRET_KEY`
3. Configure `ALLOWED_HOSTS`
4. Use a production database
5. Set up static file serving (e.g., with WhiteNoise or a web server)
6. Configure media file storage (e.g., AWS S3)
7. Use environment variables for sensitive data
8. Enable HTTPS

## Next Steps

- Add email verification
- Implement password reset functionality
- Add notification system
- Implement messaging between buyers and sellers
- Add car comparison feature
- Add review and rating system
- Implement advanced analytics
- Add admin dashboard

## License

ISC
