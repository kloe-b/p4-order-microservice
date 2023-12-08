from flask import Flask, jsonify, request
import os
import json
from flask_cors import CORS
from database import db,Ord, bcrypt
from redis import Redis
import subprocess
import logging
import time
from flask import Response
import threading
from threading import Thread
from prometheus_client import Counter, generate_latest
from opentelemetry import trace
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes


service_name = "my-order-service" 
resource = Resource(attributes={
    ResourceAttributes.SERVICE_NAME: service_name
})
app = Flask(__name__)

trace.set_tracer_provider(TracerProvider(resource=resource))

otlp_exporter = OTLPSpanExporter(
    endpoint="http://localhost:4317",  
    insecure=True
)

trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(otlp_exporter))

FlaskInstrumentor().instrument_app(app)

tracer = trace.get_tracer(__name__)

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] =\
    'sqlite:///' + os.path.join(basedir, 'database.db')

REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))

r = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
pubsub = r.pubsub()
db.init_app(app)
bcrypt.init_app(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger(__name__)

order_creation_counter = Counter('order_creation_total', 'Total number of created orders')
order_failure_counter = Counter('order_failure_total', 'Total number of failed orders')

@app.route('/metrics')
def serve_metrics():
    return Response(generate_latest(), mimetype="text/plain")

@app.route('/orders', methods=['POST'])
def create_order():
    with tracer.start_as_current_span("create_order") as span:
        try:  
            logger.info(f"Received new order:")
            data = request.json
            new_order = Ord(
                customer_id=data['customer_id'],
                product_id=data['product_id'],  
                amount=data['amount'],
                status='pending' 
            )
            span.set_attribute("customer_id", data['customer_id'])
            span.set_attribute("product_id", data['product_id'])

            with tracer.start_as_current_span("db_insert_order"):
                db.session.add(new_order)
                db.session.commit()

            order_creation_counter.inc()

            r.publish('order_created', json.dumps({'order_id': new_order.id, 'customer_id': new_order.customer_id, 'amount': new_order.amount, 'product_id': new_order.product_id}))

            return jsonify(new_order.to_dict()), 201
        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Error creating order"))
            order_failure_counter.inc()
            logger.exception("Error occurred while creating an order")
            return jsonify({'error': 'Order creation failed'}), 500


def update_order_status(order_id, status):
    with app.app_context():
        with tracer.start_as_current_span("update_order_status") as span:
            order = Ord.query.get(order_id)
            # order = db.session.get(Ord, order_id)
            if order:
                order.status = status
                with tracer.start_as_current_span("db_commit"):
                    db.session.commit()
                return jsonify(order.to_dict()), 201
            else:
                span.set_status(trace.Status(trace.StatusCode.ERROR, "Order not found"))
                return jsonify({'error': 'Order not found'}), 404

@app.route('/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    order = Ord.query.get(order_id)
    if order:
        return jsonify({'customer_id': order.customer_id, 'status': order.status, 'amount': order.amount,'product_id': order.product_id})
    else:
        return jsonify({'error': 'Order not found'}), 404

def handle_payment_status_event(data):
    logger.info(f"Handling payment status event for data: {data}")
    order_id = data['order_id']
    status = data['status']

    if status == 'SUCCESS':
        update_order_status(order_id, 'paid')
    elif status in ['INSUFFICIENT_FUND', 'TIMEOUT', 'UNKNOWN']:
        update_order_status(order_id, 'payment_failed')


def payment_status_listener():
    with tracer.start_as_current_span("payment_status_listener"):
        pubsub = r.pubsub()
        pubsub.subscribe('payment_status')
        logger.info("Listening for payment status")
        for message in pubsub.listen():
            if message['type'] == 'message':
                with tracer.start_as_current_span("handle_payment_status_event"):
                    data = json.loads(message['data'])
                    handle_payment_status_event(data)

if __name__ == '__main__':
    logger.info("Starting Flask application and initializing database")
    with app.app_context():
        db.create_all()
    logger.info("Database initialized")
    thread = threading.Thread(target=payment_status_listener)
    thread.start()
    logger.info("Background thread for payment status listener started")

    app.run(debug=True, port=5000)
