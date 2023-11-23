from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import datetime

db = SQLAlchemy()
bcrypt = Bcrypt()

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f'<Order {self.id}>'
    
    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "status": self.status,
            "amount": self.amount
        }
