from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Regexp
from datetime import datetime, timedelta
import uuid
import io
import qrcode
import smtplib
import base64
from email.mime.text import MIMEText
import requests
from flask_mail import Mail

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///erecycle.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Admin basic auth
app.config['ADMIN_USERNAME'] = 'admin'
app.config['ADMIN_PASSWORD'] = 'admin123'
app.config['ADMIN_LOGIN_URL'] = '/admin/login'

# Email / M365 configuration
app.config['MAIL_SERVER'] = 'smtp.office365.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = ''
app.config['MAIL_PASSWORD'] = ''
app.config['MAIL_DEFAULT_SENDER'] = ''
app.config['PUBLIC_BASE_URL'] = 'https://erecycle.sultantech.ca'

mail = Mail(app)
db = SQLAlchemy(app)


def send_pickup_confirmation_email(pickup):
    public_base_url = (app.config.get('PUBLIC_BASE_URL') or 'https://erecycle.sultantech.ca').rstrip('/')
    tracking_url = f"{public_base_url}/track/{pickup.tracking_number}"

    qr = qrcode.make(tracking_url, box_size=6, border=2)
    buf = io.BytesIO()
    qr.save(buf, format='PNG')
    qr_data_uri = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

    html_body = render_template(
        'email/pickup_confirmation.html',
        first_name=pickup.customer.first_name,
        tracking_number=pickup.tracking_number,
        tracking_url=tracking_url,
        preferred_date=pickup.preferred_date.strftime('%B %d, %Y'),
        time_window=pickup.preferred_time_window.replace('_', ' ').title(),
        qr_data_uri=qr_data_uri,
    )

    if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
        app.logger.warning('SMTP pickup email skipped: MAIL_USERNAME or MAIL_PASSWORD missing')
        return False

    try:
        sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
        subject = f"Pickup Request Confirmed - {pickup.tracking_number}"
        msg = MIMEText(html_body, 'html')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = pickup.customer.email

        with smtplib.SMTP(app.config.get('MAIL_SERVER'), int(app.config.get('MAIL_PORT'))) as server:
            if app.config.get('MAIL_USE_TLS'):
                server.starttls()
            server.login(app.config.get('MAIL_USERNAME'), app.config.get('MAIL_PASSWORD'))
            server.sendmail(sender, [pickup.customer.email], msg.as_string())

        app.logger.info(f"Pickup confirmation email sent to {pickup.customer.email} for {pickup.tracking_number}")
        return True
    except Exception as e:
        app.logger.error(f"SMTP pickup email failed for {pickup.tracking_number}: {e}")
        return False


def send_status_update_email(pickup, old_status=None):
    public_base_url = (app.config.get('PUBLIC_BASE_URL') or 'https://erecycle.sultantech.ca').rstrip('/')
    tracking_url = f"{public_base_url}/track/{pickup.tracking_number}"

    subject = f"Pickup Status Updated - {pickup.tracking_number}"
    body = f"""Hi {pickup.customer.first_name},

Your pickup request status has been updated.

Tracking Number: {pickup.tracking_number}
Current Status: {pickup.status.replace('_', ' ').title()}
{f"Previous Status: {old_status.replace('_', ' ').title()}" if old_status else ""}

You can track your request here: {tracking_url}

If you have any questions, please contact us with your tracking number.

Thank you for recycling responsibly!
"""

    if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
        app.logger.warning('SMTP status email skipped: MAIL_USERNAME or MAIL_PASSWORD missing')
        return False

    try:
        sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = pickup.customer.email

        with smtplib.SMTP(app.config.get('MAIL_SERVER'), int(app.config.get('MAIL_PORT'))) as server:
            if app.config.get('MAIL_USE_TLS'):
                server.starttls()
            server.login(app.config.get('MAIL_USERNAME'), app.config.get('MAIL_PASSWORD'))
            server.sendmail(sender, [pickup.customer.email], msg.as_string())

        app.logger.info(f"Status update email sent to {pickup.customer.email} for {pickup.tracking_number}: {pickup.status}")
        return True
    except Exception as e:
        app.logger.error(f"SMTP status email failed for {pickup.tracking_number}: {e}")
        return False


# ========== DATABASE MODELS ==========

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    phone = db.Column(db.String(20), nullable=True)
    address_line1 = db.Column(db.String(200), nullable=False)
    address_line2 = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(50), nullable=False)
    zip_code = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    pickup_requests = db.relationship('PickupRequest', backref='customer', lazy=True, cascade='all, delete-orphan')


class DeviceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False)  # computer, mobile, battery, etc.
    description = db.Column(db.Text, nullable=True)
    recycling_fee = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PickupRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_number = db.Column(db.String(50), unique=True, nullable=False, default=lambda: str(uuid.uuid4())[:8].upper())
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    preferred_date = db.Column(db.Date, nullable=False)
    preferred_time_window = db.Column(db.String(20), nullable=True)  # e.g., "9am-12pm"
    status = db.Column(db.String(30), default='pending', nullable=False)
    notes = db.Column(db.Text, nullable=True)
    estimated_weight = db.Column(db.Float, nullable=True)  # in kg
    admin_notes = db.Column(db.Text, nullable=True)
    scheduled_pickup_date = db.Column(db.Date, nullable=True)
    actual_pickup_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship('PickupItem', backref='pickup_request', lazy=True, cascade='all, delete-orphan')
    status_history = db.relationship('StatusUpdate', backref='pickup_request', lazy=True, cascade='all, delete-orphan')


class PickupItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pickup_request_id = db.Column(db.Integer, db.ForeignKey('pickup_request.id'), nullable=False)
    device_type_id = db.Column(db.Integer, db.ForeignKey('device_type.id'), nullable=True)
    custom_device_name = db.Column(db.String(200), nullable=True)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    condition = db.Column(db.String(50), nullable=True)  # working, broken, damaged
    weight_estimate = db.Column(db.Float, nullable=True)  # per item
    notes = db.Column(db.Text, nullable=True)

    device_type = db.relationship('DeviceType', backref='pickup_items')


class StatusUpdate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pickup_request_id = db.Column(db.Integer, db.ForeignKey('pickup_request.id'), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(100), default='system')


class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)


# ========== FORMS ==========

class PickupRequestForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired(), Length(min=2, max=100)])
    last_name = StringField('Last Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    phone = StringField('Phone (optional)', validators=[Length(max=20)])
    address_line1 = StringField('Street Address', validators=[DataRequired(), Length(max=200)])
    address_line2 = StringField('Apt/Suite (optional)', validators=[Length(max=200)])
    city = StringField('City', validators=[DataRequired(), Length(max=100)])
    state = StringField('State', validators=[DataRequired(), Length(max=50)])
    zip_code = StringField('ZIP Code', validators=[DataRequired(), Length(max=20)])
    preferred_date = StringField('Preferred Pickup Date', validators=[DataRequired()])
    preferred_time_window = SelectField('Preferred Time Window', choices=[
        ('morning', 'Morning (9am-12pm)'),
        ('afternoon', 'Afternoon (12pm-3pm)'),
        ('evening', 'Late Afternoon (3pm-6pm)'),
        ('flexible', 'Flexible / Any Time'),
    ])
    estimated_weight = StringField('Estimated Total Weight (kg)', validators=[Length(max=20)])
    notes = TextAreaField('Additional Notes', validators=[Length(max=1000)])
    submit = SubmitField('Submit Pickup Request')


class AdminStatusUpdateForm(FlaskForm):
    status = SelectField('New Status', choices=[
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('scheduled', 'Scheduled'),
        ('picked_up', 'Picked Up'),
        ('processing', 'Processing / Received'),
        ('recycled', 'Recycled / Completed'),
        ('cancelled', 'Cancelled'),
    ])
    admin_notes = TextAreaField('Notes', validators=[Length(max=1000)])
    scheduled_pickup_date = StringField('Schedule Pickup Date', validators=[Length(max=20)])
    submit = SubmitField('Update Status')


# ========== HELPER FUNCTIONS ==========

def initialize_device_types():
    default_types = [
        ("Laptop", "computer", "Laptops and notebook computers", 5.0),
        ("Desktop Computer", "computer", "Desktop towers, all-in-ones, monitors", 8.0),
        ("Monitor / Display", "display", "LCD, LED, CRT monitors and TVs", 10.0),
        ("Smartphone", "mobile", "Smartphones, feature phones", 2.0),
        ("Tablet", "mobile", "iPads, Android tablets, e-readers", 2.0),
        ("Printer", "peripheral", "Inkjet, laser, multifunction printers", 5.0),
        ("Batteries (Lithium-ion)", "battery", "Rechargeable Li-ion battery packs", 3.0),
        ("Power Supplies", "component", "Power supplies, adapters, chargers", 1.0),
        ("Cables / Wiring", "material", "Power cords, data cables, wiring", 0.5),
        ("Other Electronics", "other", "Other electronic devices and components", 3.0),
    ]
    for name, category, desc, fee in default_types:
        if not DeviceType.query.filter_by(name=name).first():
            device = DeviceType(name=name, category=category, description=desc, recycling_fee=fee)
            db.session.add(device)
    db.session.commit()


def create_status_update(pickup_request_id, new_status, notes=None, created_by='system'):
    update = StatusUpdate(
        pickup_request_id=pickup_request_id,
        status=new_status,
        notes=notes,
        created_by=created_by
    )
    db.session.add(update)
    db.session.commit()
    return update


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/request-pickup', methods=['GET', 'POST'])
def request_pickup():
    form = PickupRequestForm()
    if form.validate_on_submit():
        # Create or find customer
        customer = Customer.query.filter_by(email=form.email.data).first()
        if not customer:
            customer = Customer(
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                email=form.email.data,
                phone=form.phone.data,
                address_line1=form.address_line1.data,
                address_line2=form.address_line2.data,
                city=form.city.data,
                state=form.state.data,
                zip_code=form.zip_code.data,
            )
            db.session.add(customer)
            db.session.flush()
        else:
            # Update contact info if changed
            customer.first_name = form.first_name.data
            customer.last_name = form.last_name.data
            customer.phone = form.phone.data
            customer.address_line1 = form.address_line1.data
            customer.address_line2 = form.address_line2.data
            customer.city = form.city.data
            customer.state = form.state.data
            customer.zip_code = form.zip_code.data

        # Create pickup request
        preferred_date = datetime.strptime(form.preferred_date.data, '%Y-%m-%d').date()
        estimated_weight_raw = (form.estimated_weight.data or '').strip()
        estimated_weight = None
        if estimated_weight_raw:
            import re
            numeric_match = re.search(r'[0-9]+(\.[0-9]+)?', estimated_weight_raw)
            if numeric_match:
                estimated_weight = float(numeric_match.group())

        pickup = PickupRequest(
            customer_id=customer.id,
            preferred_date=preferred_date,
            preferred_time_window=form.preferred_time_window.data,
            estimated_weight=estimated_weight,
            notes=form.notes.data,
        )
        db.session.add(pickup)
        db.session.flush()

        create_status_update(pickup.id, 'pending', 'Pickup request submitted via website.')
        db.session.commit()

        mail_sent = False
        try:
            mail_sent = send_pickup_confirmation_email(pickup)
        except Exception:
            mail_sent = False

        if mail_sent:
            flash(f'Pickup request submitted! Your tracking number is: {pickup.tracking_number}. A confirmation email has been sent.', 'success')
        else:
            flash(f'Pickup request submitted! Your tracking number is: {pickup.tracking_number}', 'success')
        return redirect(url_for('track_request', tracking_number=pickup.tracking_number))

    return render_template('request_pickup.html', form=form)


@app.route('/track')
def track_form():
    tracking_number = request.args.get('tracking_number')
    if tracking_number:
        return redirect(url_for('track_request', tracking_number=tracking_number))
    return render_template('track_form.html')


@app.route('/track/<tracking_number>')
def track_request(tracking_number):
    pickup = PickupRequest.query.filter_by(tracking_number=tracking_number).first_or_404()
    return render_template('track_request.html', pickup=pickup)


@app.route('/api/track/<tracking_number>')
def api_track_request(tracking_number):
    pickup = PickupRequest.query.filter_by(tracking_number=tracking_number).first_or_404()
    history = [
        {
            'status': u.status,
            'notes': u.notes,
            'timestamp': u.created_at.isoformat(),
            'created_by': u.created_by
        }
        for u in StatusUpdate.query.filter_by(pickup_request_id=pickup.id).order_by(StatusUpdate.created_at).all()
    ]
    return jsonify({
        'tracking_number': pickup.tracking_number,
        'status': pickup.status,
        'customer': {
            'name': f"{pickup.customer.first_name} {pickup.customer.last_name}",
            'email': pickup.customer.email,
            'phone': pickup.customer.phone,
            'address': f"{pickup.customer.address_line1}, {pickup.customer.city}, {pickup.customer.state} {pickup.customer.zip_code}"
        },
        'preferred_date': pickup.preferred_date.isoformat(),
        'preferred_time_window': pickup.preferred_time_window,
        'scheduled_pickup_date': pickup.scheduled_pickup_date.isoformat() if pickup.scheduled_pickup_date else None,
        'actual_pickup_date': pickup.actual_pickup_date.isoformat() if pickup.actual_pickup_date else None,
        'admin_notes': pickup.admin_notes,
        'items': [
            {
                'name': item.device_type.name if item.device_type else item.custom_device_name,
                'quantity': item.quantity,
                'condition': item.condition,
                'notes': item.notes
            }
            for item in pickup.items
        ],
        'status_history': history,
        'created_at': pickup.created_at.isoformat(),
    })


@app.route('/api/reverse-geocode')
def api_reverse_geocode():
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)

    if lat is None or lng is None:
        return jsonify({'error': 'lat and lng are required'}), 400

    session = requests.Session()
    http_proxy = app.config.get('HTTP_PROXY') or app.config.get('http_proxy') or get_app_setting('http_proxy', '') or ''
    https_proxy = app.config.get('HTTPS_PROXY') or app.config.get('https_proxy') or get_app_setting('https_proxy', '') or ''
    proxies = {}
    if http_proxy:
        proxies['http'] = http_proxy
    if https_proxy:
        proxies['https'] = https_proxy
    if proxies:
        session.proxies = proxies

    errors = []

    # Try Nominatim first with structured address parsing
    try:
        resp = session.get(
            'https://nominatim.openstreetmap.org/reverse',
            params={'lat': lat, 'lon': lng, 'format': 'json', 'addressdetails': 1},
            headers={'User-Agent': 'EcoRecycle-App/1.0'},
            timeout=20
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get('error') is None:
                address = data.get('address', {})

                # Build street from structured components
                street = ''
                house_number = address.get('house_number', '')
                road = address.get('road', '')
                pedestrian = address.get('pedestrian', '')
                street_name = road or pedestrian or ''

                if house_number and street_name:
                    street = f"{house_number} {street_name}"
                elif house_number:
                    street = house_number
                elif street_name:
                    street = street_name

                # Fallback to display_name street portion
                display_name = data.get('display_name', '') or ''
                if not street and display_name:
                    first_part = display_name.split(',')[0].strip()
                    street = first_part if first_part else ''

                city = address.get('city', address.get('town', address.get('village', address.get('county', ''))))
                state = address.get('state', '')
                zip_code = address.get('postcode', '')

                # Reject results that are too coarse: require street + city or more detail
                parts = [p for p in [street, city, state, zip_code] if p]
                if len(parts) >= 2 and street:
                    return jsonify({
                        'street': street.strip(),
                        'city': city,
                        'state': state,
                        'zip': zip_code,
                        'display_name': display_name,
                        'provider': 'nominatim'
                    })
                else:
                    errors.append(f"Nominatim returned coarse address: {display_name}")
            else:
                errors.append(f"Nominatim error: {data.get('error')}")
        else:
            errors.append(f"Nominatim HTTP {resp.status_code}")
    except Exception as e:
        errors.append(f"Nominatim exception: {str(e)}")

    # Try Google Maps if API key is configured
    google_api_key = app.config.get('GOOGLE_MAPS_API_KEY', '') or get_app_setting('google_maps_api_key', '')
    if google_api_key:
        try:
            google_resp = session.get(
                'https://maps.googleapis.com/maps/api/geocode/json',
                params={'latlng': f'{lat},{lng}', 'key': google_api_key},
                timeout=20
            )
            google_data = google_resp.json()
            if google_data.get('status') == 'OK' and google_data.get('results'):
                result = google_data['results'][0]
                address_components = {}
                for comp in result.get('address_components', []):
                    for type_ in comp.get('types', []):
                        address_components[type_] = comp.get('long_name', '')

                street_number = address_components.get('street_number', '')
                route = address_components.get('route', '')
                street = f"{street_number} {route}".strip()
                city = address_components.get('locality', address_components.get('administrative_area_level_3', ''))
                state = address_components.get('administrative_area_level_1', '')
                zip_code = address_components.get('postal_code', '')

                parts = [p for p in [street, city, state, zip_code] if p]
                if len(parts) >= 2 and street:
                    return jsonify({
                        'street': street,
                        'city': city,
                        'state': state,
                        'zip': zip_code,
                        'display_name': result.get('formatted_address', ''),
                        'provider': 'google'
                    })
                else:
                    errors.append(f"Google returned coarse address: {result.get('formatted_address', '')}")
            else:
                errors.append(f"Google status: {google_data.get('status')}")
        except Exception as e:
            errors.append(f"Google exception: {str(e)}")

    error_msg = 'Geocoding failed.'
    if errors:
        error_msg += ' Details: ' + ' | '.join(errors)
    return jsonify({'error': error_msg}), 500


# ========== ADMIN ROUTES ==========

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_authenticated'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    class LoginForm(FlaskForm):
        class Meta:
            csrf = False
        username = StringField('Username', validators=[DataRequired()])
        password = StringField('Password', validators=[DataRequired()])
        submit = SubmitField('Sign In')
    form = LoginForm()
    if form.validate_on_submit():
        if form.username.data == app.config.get('ADMIN_USERNAME') and form.password.data == app.config.get('ADMIN_PASSWORD'):
            session['admin_authenticated'] = True
            next_url = request.args.get('next') or url_for('admin_dashboard')
            return redirect(next_url)
        flash('Invalid credentials.', 'danger')
    return render_template('admin/login.html', form=form)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_authenticated', None)
    flash('Logged out.', 'success')
    return redirect(url_for('admin_login'))

@app.route('/admin')
@app.route('/admin/', strict_slashes=False)
@admin_required
def admin_dashboard():
    # Summary statistics
    total = PickupRequest.query.count()
    pending = PickupRequest.query.filter_by(status='pending').count()
    scheduled = PickupRequest.query.filter_by(status='scheduled').count()
    picked_up = PickupRequest.query.filter_by(status='picked_up').count()
    recycled = PickupRequest.query.filter_by(status='recycled').count()
    cancelled = PickupRequest.query.filter_by(status='cancelled').count()

    today = datetime.utcnow().date()
    upcoming = PickupRequest.query.filter(
        PickupRequest.scheduled_pickup_date >= today,
        PickupRequest.status.in_(['scheduled', 'pending', 'confirmed'])
    ).order_by(PickupRequest.scheduled_pickup_date).limit(10).all()

    stats = {
        'total': total, 'pending': pending, 'scheduled': scheduled,
        'picked_up': picked_up, 'recycled': recycled, 'cancelled': cancelled
    }
    return render_template('admin/dashboard.html', stats=stats, upcoming=upcoming)


@app.route('/admin/requests')
@admin_required
def admin_requests():
    status_filter = request.args.get('status', 'all')
    query = PickupRequest.query
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    requests = query.order_by(PickupRequest.created_at.desc()).limit(100).all()
    return render_template('admin/requests.html', requests=requests, status_filter=status_filter)


@app.route('/admin/request/<int:id>', methods=['GET', 'POST'])
@admin_required
def admin_request_detail(id):
    pickup = PickupRequest.query.get_or_404(id)
    form = AdminStatusUpdateForm(obj=pickup)
    device_types = DeviceType.query.order_by(DeviceType.category, DeviceType.name).all()

    if form.validate_on_submit():
        old_status = pickup.status
        pickup.status = form.status.data
        pickup.admin_notes = form.admin_notes.data
        if form.scheduled_pickup_date.data:
            pickup.scheduled_pickup_date = datetime.strptime(form.scheduled_pickup_date.data, '%Y-%m-%d').date()
        if form.status.data == 'picked_up' and not pickup.actual_pickup_date:
            pickup.actual_pickup_date = datetime.utcnow().date()
        db.session.commit()

        create_status_update(
            pickup.id,
            form.status.data,
            f"Status changed from {old_status} to {form.status.data}. {form.admin_notes.data or ''}".strip(),
            created_by='admin'
        )
        send_status_update_email(pickup, old_status=old_status)
        flash(f'Request {pickup.tracking_number} updated to "{form.status.data}"', 'success')
        return redirect(url_for('admin_request_detail', id=id))

    return render_template('admin/request_detail.html', pickup=pickup, form=form, device_types=device_types)


@app.route('/admin/request/<int:id>/add-item', methods=['POST'])
@admin_required
def admin_add_item(id):
    pickup = PickupRequest.query.get_or_404(id)
    device_type_id = request.form.get('device_type_id')
    custom_name = request.form.get('custom_device_name')
    quantity = int(request.form.get('quantity', 1))
    condition = request.form.get('condition', 'unknown')
    weight = request.form.get('weight_estimate')
    notes = request.form.get('notes')

    item = PickupItem(
        pickup_request_id=pickup.id,
        device_type_id=int(device_type_id) if device_type_id else None,
        custom_device_name=custom_name if not device_type_id else None,
        quantity=quantity,
        condition=condition,
        weight_estimate=float(weight) if weight else None,
        notes=notes
    )
    db.session.add(item)
    db.session.commit()
    flash('Item added to request', 'success')
    return redirect(url_for('admin_request_detail', id=id))


@app.route('/admin/request/<int:id>/update-customer', methods=['POST'])
@admin_required
def admin_update_customer(id):
    pickup = PickupRequest.query.get_or_404(id)
    customer = pickup.customer

    customer.first_name = request.form.get('first_name', '').strip()
    customer.last_name = request.form.get('last_name', '').strip()
    customer.email = request.form.get('email', '').strip()
    customer.phone = request.form.get('phone', '').strip() or None
    customer.address_line1 = request.form.get('address_line1', '').strip()
    customer.address_line2 = request.form.get('address_line2', '').strip() or None
    customer.city = request.form.get('city', '').strip()
    customer.state = request.form.get('state', '').strip()
    customer.zip_code = request.form.get('zip_code', '').strip()

    db.session.commit()
    flash('Customer information updated', 'success')
    return redirect(url_for('admin_request_detail', id=id))


@app.route('/admin/request/<int:item_id>/delete-item', methods=['POST'])
@admin_required
def admin_delete_item(item_id):
    item = PickupItem.query.get_or_404(item_id)
    pickup_id = item.pickup_request_id
    db.session.delete(item)
    db.session.commit()
    flash('Item removed', 'success')
    return redirect(url_for('admin_request_detail', id=pickup_id))


@app.route('/admin/device-types')
@admin_required
def admin_device_types():
    device_types = DeviceType.query.order_by(DeviceType.category, DeviceType.name).all()
    return render_template('admin/device_types.html', device_types=device_types)


@app.route('/admin/device-types/add', methods=['POST'])
@admin_required
def admin_add_device_type():
    name = request.form.get('name')
    category = request.form.get('category', 'other')
    desc = request.form.get('description')
    fee = float(request.form.get('recycling_fee', 0))
    if name and not DeviceType.query.filter_by(name=name).first():
        device = DeviceType(name=name, category=category, description=desc, recycling_fee=fee)
        db.session.add(device)
        db.session.commit()
        flash(f'Device type "{name}" added', 'success')
    else:
        flash('Device type already exists', 'danger')
    return redirect(url_for('admin_device_types'))


@app.route('/admin/device-types/<int:id>/delete', methods=['POST'])
@admin_required
def admin_delete_device_type(id):
    device = DeviceType.query.get_or_404(id)
    db.session.delete(device)
    db.session.commit()
    flash(f'Device type "{device.name}" deleted', 'success')
    return redirect(url_for('admin_device_types'))


@app.route('/admin/request/<int:id>/print-label')
@admin_required
def admin_print_label(id):
    pickup = PickupRequest.query.get_or_404(id)
    return render_template('admin/print_label.html', pickup=pickup)


@app.route('/qr/<tracking_number>')
def qr_code_png(tracking_number):
    pickup = PickupRequest.query.filter_by(tracking_number=tracking_number).first_or_404()
    tracking_url = url_for('track_request', tracking_number=pickup.tracking_number, _external=True)
    img = qrcode.make(tracking_url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    from flask import Response
    return Response(buf.getvalue(), mimetype='image/png')


# ========== EMAIL SETTINGS ==========

def get_app_setting(key, default=None):
    setting = AppSetting.query.filter_by(key=key).first()
    return setting.value if setting else default


def set_app_setting(key, value):
    setting = AppSetting.query.filter_by(key=key).first()
    if setting:
        setting.value = value
    else:
        setting = AppSetting(key=key, value=value)
        db.session.add(setting)
    db.session.commit()


def apply_email_settings():
    app.config['MAIL_SERVER'] = get_app_setting('mail_server', 'smtp.office365.com')
    app.config['MAIL_PORT'] = int(get_app_setting('mail_port', '587'))
    app.config['MAIL_USE_TLS'] = get_app_setting('mail_use_tls', 'true').lower() == 'true'
    app.config['MAIL_USERNAME'] = get_app_setting('mail_username', '')
    app.config['MAIL_PASSWORD'] = get_app_setting('mail_password', '')
    app.config['MAIL_DEFAULT_SENDER'] = get_app_setting('mail_default_sender', '') or get_app_setting('mail_username', '')
    app.config['PUBLIC_BASE_URL'] = get_app_setting('public_base_url', '') or 'https://erecycle.sultantech.ca'


@app.route('/admin/email-settings', methods=['GET', 'POST'])
def admin_email_settings():
    settings = {
        'mail_server': get_app_setting('mail_server', 'smtp.office365.com'),
        'mail_port': get_app_setting('mail_port', '587'),
        'mail_use_tls': get_app_setting('mail_use_tls', 'true'),
        'mail_username': get_app_setting('mail_username', ''),
        'mail_password': get_app_setting('mail_password', ''),
        'mail_default_sender': get_app_setting('mail_default_sender', ''),
        'public_base_url': get_app_setting('public_base_url', 'https://erecycle.sultantech.ca'),
    }

    if request.method == 'POST':
        set_app_setting('mail_server', request.form.get('mail_server', 'smtp.office365.com'))
        set_app_setting('mail_port', request.form.get('mail_port', '587'))
        set_app_setting('mail_use_tls', request.form.get('mail_use_tls', 'true'))
        set_app_setting('mail_username', request.form.get('mail_username', '').strip())
        set_app_setting('mail_password', request.form.get('mail_password', '').strip())
        set_app_setting('mail_default_sender', request.form.get('mail_default_sender', '').strip())
        set_app_setting('public_base_url', request.form.get('public_base_url', '').strip())

        apply_email_settings()
        flash('Email settings saved', 'success')
        return redirect(url_for('admin_email_settings'))

    return render_template('admin/email_settings.html', settings=settings)


@app.route('/admin/email-settings/test', methods=['POST'])
def admin_test_email():
    test_email = request.form.get('test_email')
    if not test_email:
        flash('Please provide a test email address', 'danger')
        return redirect(url_for('admin_email_settings'))

    if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
        flash('Email settings are incomplete. Please configure SMTP first.', 'danger')
        return redirect(url_for('admin_email_settings'))

    try:
        sender = app.config.get('MAIL_DEFAULT_SENDER') or app.config.get('MAIL_USERNAME')
        msg = MIMEText('This is a test email from EcoRecycle. Your M365 settings are working correctly.')
        msg['Subject'] = 'EcoRecycle Test Email'
        msg['From'] = sender
        msg['To'] = test_email

        with smtplib.SMTP(app.config.get('MAIL_SERVER'), int(app.config.get('MAIL_PORT'))) as server:
            if app.config.get('MAIL_USE_TLS'):
                server.starttls()
            server.login(app.config.get('MAIL_USERNAME'), app.config.get('MAIL_PASSWORD'))
            server.sendmail(sender, [test_email], msg.as_string())

        flash(f'Test email sent to {test_email}', 'success')
    except Exception as e:
        flash(f'Failed to send test email: {str(e)}', 'danger')

    return redirect(url_for('admin_email_settings'))


# ========== DATABASE INITIALIZATION ==========

def init_db():
    with app.app_context():
        db.create_all()
        initialize_device_types()
        print("Database initialized!")


if __name__ == '__main__':
    import sys
    if '--init-db' in sys.argv:
        init_db()
    else:
        app.run(debug=True, port=5000)
