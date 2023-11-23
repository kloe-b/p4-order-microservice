from flask import Flask, request, jsonify, Blueprint
from database import Order, db
from redis import Redis
import json
import os
import logging
from opentelemetry import trace
from opentelemetry import metrics

order_service = Blueprint("order_service", __name__)

SECRET_KEY = 'your_secret_key'
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))

r = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
pubsub = r.pubsub()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tracer = trace.get_tracer("...tracer")
meter = metrics.get_meter("...meter")

def handle_payment_failure_event(message):
    order_id = json.loads(message['data'])['order_id']
    get_order(order_id)
   
pubsub.subscribe(**{'payment_failure': handle_payment_failure_event})
pubsub.run_in_thread(sleep_time=0.001)
 
@order_service.route('/')
def home():

    return "Orders Microservice is running!"

@order_service.route('/orders', methods=['POST'])
def create_order():
    data = request.json
    new_order = Order(customer_id=data['customer_id'], status='pending', amount=data['amount'])
    db.session.add(new_order)
    db.session.commit()
    user = Order.query.filter_by(customer_id=data['customer_id']).count()
    if user>1:
        r.publish('order_created', json.dumps({'customer_id': new_order.customer_id,'new': False,'amount': new_order.amount}))
    else:
         r.publish('order_created', json.dumps({'customer_id': new_order.customer_id,'new': True,'amount': new_order.amount}))
    

    return jsonify({'id': new_order.id}), 201


@order_service.route('/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    order = Order.query.get(order_id)
    if order:
        # listen_for_payment_rollbacks()
        return jsonify({'customer_id': order.customer_id, 'status': order.status, 'amount': order.amount})
    else:
        return jsonify({'error': 'Order not found'}), 404

@order_service.route('/user-exists/<int:user_id>', methods=['GET'])
def user_exists(user_id):
    user = Order.query.filter_by(customer_id=user_id).first()
    if user:
        return jsonify({'exists': True}), 200
    else:
        return jsonify({'exists': False}), 404

@order_service.route('/orders/<int:order_id>', methods=['PUT'])
def update_order(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    r.set(f'order_{order_id}', json.dumps(order.to_dict()))

    try:
        order_data = request.json
        order.customer_id = order_data.get('customer_id', order.customer_id)
        order.status = order_data.get('status', order.status)
        order.amount = order_data.get('amount', order.amount)

        db.session.commit()
        return jsonify({'id': order.id, 'customer_id': order.customer_id, 'status': order.status, 'amount': order.amount})
    
    except Exception as e:
        old_order_data = json.loads(r.get(f'order_{order_id}'))
        order.customer_id = old_order_data['customer_id']
        order.status = old_order_data['status']
        order.amount = old_order_data['amount']
        db.session.commit()
        return jsonify({'error': 'Update failed, rolled back to previous state'}), 500


@order_service.route('/orders/<int:order_id>', methods=['DELETE'])
def delete_order(order_id):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    r.set(f'order_{order_id}', json.dumps(order.to_dict()))

    try:
        db.session.delete(order)
        db.session.commit()
        return jsonify({'message': 'Order deleted'})

    except Exception as e:
        old_order_data = json.loads(r.get(f'order_{order_id}'))
        rollback_order = Order(**old_order_data)
        db.session.add(rollback_order)
        db.session.commit()
        return jsonify({'error': 'Deletion failed, rolled back to previous state'}), 500



