from flask import Flask, jsonify, request
import os
import json
from flask_cors import CORS
from database import db,Ord, bcrypt
from redis import Redis
import subprocess
import logging
import time
import threading
from threading import Thread

app = Flask(__name__)

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] =\
    'sqlite:///' + os.path.join(basedir, 'database.db')
 
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))

r = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
pubsub = r.pubsub()
db.init_app(app)
bcrypt.init_app(app)

@app.route('/orders', methods=['POST'])
def create_order():
    data = request.json
    new_order = Ord(
        customer_id=data['customer_id'],
        product_id=data['product_id'],  
        amount=data['amount'],
        status='pending' 
    )
    db.session.add(new_order)
    db.session.commit()

    r.publish('order_created', json.dumps({'order_id': new_order.id, 'customer_id': new_order.customer_id, 'amount': new_order.amount, 'product_id': new_order.product_id}))

    return jsonify(new_order.to_dict()), 201


def update_order_status(order_id, status):
    with app.app_context():
        order = db.session.get(Ord, order_id)
        if order:
            order.status = status
            db.session.commit()
            return jsonify(order.to_dict()), 201

@app.route('/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    order = Ord.query.get(order_id)
    if order:
        return jsonify({'customer_id': order.customer_id, 'status': order.status, 'amount': order.amount,'product_id': new_order.product_id})
    else:
        return jsonify({'error': 'Order not found'}), 404

def handle_payment_status_event(data):
    order_id = data['order_id']
    status = data['status']

    if status == 'SUCCESS':
        update_order_status(order_id, 'paid')
    elif status in ['INSUFFICIENT_FUND', 'TIMEOUT', 'UNKNOWN']:
        update_order_status(order_id, 'payment_failed')

r.pubsub().subscribe(**{'payment_status': handle_payment_status_event})
def payment_status_listener():
    pubsub = r.pubsub()
    pubsub.subscribe('payment_status')

    for message in pubsub.listen():
        if message['type'] == 'message':
            data = json.loads(message['data'])
            handle_payment_status_event(data)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    thread = threading.Thread(target=payment_status_listener)
    thread.start()

    app.run(debug=True, port=5000)
