from app import app, db, initialize_device_types

with app.app_context():
    db.create_all()
    initialize_device_types()
    print('Database initialized with device types')
