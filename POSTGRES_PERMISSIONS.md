# PostgreSQL permissions for car_user

If you see **"permission denied for schema public"** when running `python manage.py migrate`, run the following as a superuser (e.g. `postgres`).

Connect:

```bash
psql postgres
```

Then run:

```sql
\c car_market
GRANT ALL ON SCHEMA public TO car_user;
GRANT CREATE ON SCHEMA public TO car_user;
ALTER SCHEMA public OWNER TO car_user;
\q
```

Then run migrations again:

```bash
python3 manage.py migrate
```
