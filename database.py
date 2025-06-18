# database.py
from typing import List

import pymongo
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

# Import necessary types from bson library (part of pymongo)
from bson.decimal128 import Decimal128
from bson.codec_options import CodecOptions, TypeRegistry, TypeCodec
import bson # Required for Decimal128 type check below

# --- Custom Codec for Decimal <-> Decimal128 ---
# This tells pymongo how to handle Python's Decimal type automatically

class DecimalCodec(TypeCodec):
    python_type = Decimal    # the Python type acted upon by this type codec
    bson_type = Decimal128   # the BSON type acted upon by this type codec

    def transform_python(self, value):
        """Functioneltzer transforming Python Decimal to BSON Decimal128."""
        # Convert Python Decimal to BSON Decimal128
        try:
            return Decimal128(value)
        except InvalidOperation:
            # Handle potential conversion issues (e.g., NaN, Infinity) if necessary
            # For simplicity, we might raise an error or return a default
            raise ValueError(f"Cannot convert Decimal to Decimal128: {value}")


    def transform_bson(self, value):
        """Function transforming BSON Decimal128 to Python Decimal."""
        # Convert BSON Decimal128 to Python Decimal
        return value.to_decimal()

# Create TypeRegistry and CodecOptions
decimal_codec = DecimalCodec()
type_registry = TypeRegistry([decimal_codec])
codec_options = CodecOptions(type_registry=type_registry)


# --- Configuration ---
MONGO_URI = "mongodb://localhost:27017/"
DATABASE_NAME = "beverage_store"

# --- Database Connection ---
_db = None
_client = None # Keep client reference for cleanup if needed

def get_database():
    """Connects to MongoDB, applies Decimal codec, and returns the database object."""
    global _db, _client
    if _db is None:
        try:
            _client = pymongo.MongoClient(MONGO_URI)
            # The ismaster command is cheap and does not require auth.
            _client.admin.command('ismaster') # Check connection

            # Get the database *with* the codec options
            _db = _client.get_database(
                DATABASE_NAME,
                codec_options=codec_options # <-- APPLY CODEC OPTIONS HERE
            )

            print("Database connected successfully with Decimal support.")

            # Initialize cash on hand if it doesn't exist - Now uses Decimal directly
            cash_label = {"label": "cash_on_hand"}
            if _db.values.find_one(cash_label) is None:
                 _db.values.update_one(
                    cash_label,
                    {"$set": {"value": Decimal("100.00")}}, # Use Decimal directly
                    upsert=True
                )
                 print("Initialized cash on hand.")
            print(f"Connected to database: {DATABASE_NAME}")

        except pymongo.errors.ConnectionFailure as e:
            print(f"Could not connect to MongoDB: {e}")
            _db = None
            _client = None
        except Exception as e:
            # Catching potential errors during codec application or initial find/update
            print(f"An error occurred during DB initialization or connection: {e}")
            _db = None
            _client = None # Ensure client is also reset on error
    return _db

# --- Data Access Functions ---
# These functions can now mostly assume Decimal works seamlessly

def get_products() -> List[dict]:
    """Fetches all products from the database. Assumes prices are stored correctly."""
    db = get_database()
    if db is None: return []
    products = []
    try:
        # Codec handles conversion from Decimal128 back to Decimal automatically
        for p in db.products.find().sort("name", 1):
            # Ensure fields exist and provide default Decimal(0) if not
            p['crate_price'] = p.get('crate_price', Decimal('0.00'))
            p['bottle_price'] = p.get('bottle_price', Decimal('0.00'))
            p['crate_deposit'] = p.get('crate_deposit', Decimal('0.00'))
            p['bottle_deposit'] = p.get('bottle_deposit', Decimal('0.00'))
            # Important: Ensure they are Decimal if they came from DB without codec initially
            for key in ['crate_price', 'bottle_price', 'crate_deposit', 'bottle_deposit']:
                if not isinstance(p[key], Decimal):
                    try:
                        # Attempt conversion if stored as float/string previously
                        p[key] = Decimal(str(p[key]))
                    except (InvalidOperation, TypeError):
                         print(f"Warning: Invalid format for {key} in product {p.get('_id')}. Using 0.00.")
                         p[key] = Decimal('0.00') # Fallback to 0 if conversion fails
            products.append(p)
    except Exception as e:
        print(f"Error fetching products: {e}")
        return [] # Return empty list on error
    return products

def get_empties():
    """Fetches all empties from the database. Assumes values are stored correctly."""
    db = get_database()
    if db is None: return []
    empties = []
    try:
        # Codec handles conversion from Decimal128 back to Decimal automatically
        for e in db.empties.find().sort("name", 1):
            e['deposit_value'] = e.get('deposit_value', Decimal('0.00'))
            # Ensure Decimal type after retrieval
            if not isinstance(e['deposit_value'], Decimal):
                 try:
                     e['deposit_value'] = Decimal(str(e['deposit_value']))
                 except (InvalidOperation, TypeError):
                     print(f"Warning: Invalid format for deposit_value in empty {e.get('_id')}. Using 0.00.")
                     e['deposit_value'] = Decimal('0.00')
            empties.append(e)
    except Exception as e:
        print(f"Error fetching empties: {e}")
        return []
    return empties

def get_cash_on_hand():
    """Gets the current cash on hand as Decimal."""
    db = get_database()
    if db is None: return Decimal("0.00")
    try:
        cash_doc = db.values.find_one({"label": "cash_on_hand"})
        if cash_doc and 'value' in cash_doc:
            # Codec should handle the conversion if stored as Decimal128
            value = cash_doc['value']
            if isinstance(value, Decimal):
                return value
            # Handle potential legacy float/string storage if codec wasn't active before
            elif isinstance(value, (float, int, str)):
                 try:
                    return Decimal(str(value))
                 except InvalidOperation:
                     print(f"Warning: Invalid cash format found: {value}. Returning 0.00")
                     return Decimal("0.00")
            # Handle direct Decimal128 if somehow read without codec
            elif isinstance(value, bson.Decimal128):
                return value.to_decimal()
            else:
                 print(f"Warning: Unknown type for cash value: {type(value)}. Returning 0.00")
                 return Decimal("0.00") # Fallback for unexpected types
        else:
            # If doc exists but no value, or doc doesn't exist
             print("Warning: Cash on hand document not found or value missing. Returning 0.00")
             return Decimal("0.00") # Should have been initialized, but safety check
    except Exception as e:
        print(f"Error getting cash on hand: {e}")
        return Decimal("0.00")


def update_cash_on_hand(amount_change: Decimal):
    """Updates cash on hand by a given Decimal amount."""
    db = get_database()
    if db is None: return False
    try:
        # Ensure amount_change is Decimal
        change = Decimal(str(amount_change)) # Convert just in case input wasn't Decimal
        # Use $inc for atomic update - MongoDB handles Decimal128 correctly here with the codec
        result = db.values.update_one(
            {"label": "cash_on_hand"},
            {"$inc": {"value": change}}, # Use $inc with Decimal - codec handles conversion
            upsert=True # Ensure it exists if somehow deleted
        )
        return result.modified_count > 0 or result.upserted_id is not None
    except Exception as e:
        print(f"Error updating cash: {e}")
        return False


def add_transaction(transaction: dict):
    """Adds a transaction record to the database using Decimals."""
    db = get_database()
    if db is None: return False
    try:
        db.transactions.insert_one(transaction)
        return True
    except Exception as e:
        print(f"Error adding transaction: {e}")
        return False