from app import app, db
import threading

def run_on_port(port: int):
    app.run(debug=True, host='0.0.0.0', port=port, use_reloader=False)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    t1 = threading.Thread(target=run_on_port, args=(5000,), daemon=True)
    t2 = threading.Thread(target=run_on_port, args=(5001,), daemon=True)
    t1.start()
    t2.start()

    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
