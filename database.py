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
        # Ensure Decimals in items list are correctly handled (should be if coming from CLI state)
        # Optional: Add explicit check/conversion here if unsure about item structure
        for item in transaction["items"]:
            for key, value in item.items():
                if isinstance(value, (float, int)): # Convert legacy numerics if needed
                   if key in ['unit_price', 'unit_deposit', 'total_item_price']:
                       try:
                           item[key] = Decimal(str(value))
                       except InvalidOperation:
                            print(f"Warning: Invalid numeric format in item {item.get('name')}, key {key}. Storing as 0.")
                            item[key] = Decimal('0.00') # Fallback

        db.transactions.insert_one(transaction)
        return True
    except Exception as e:
        print(f"Error adding transaction: {e}")
        return False

# --- Seeding Function (Optional) ---
def seed_database():
    """Adds initial data using Decimals if collections are empty."""
    db = get_database()
    if db is None:
        print("Cannot seed, database connection failed.")
        return

    # Use Decimal for all monetary values
    # Seed Products
    if db.products.count_documents({}) == 0:
        print("Seeding products...")
        products_data = [
            {"name": "Cola Crate", "crate_price": Decimal("12.00"), "bottle_price": Decimal("0.0"), "crate_deposit": Decimal("5.00"), "bottle_deposit": Decimal("0.0")},
            {"name": "Cola Bottle", "crate_price": Decimal("0.0"), "bottle_price": Decimal("1.00"), "crate_deposit": Decimal("0.0"), "bottle_deposit": Decimal("0.15")},
            {"name": "Water Crate", "crate_price": Decimal("10.00"), "bottle_price": Decimal("0.0"), "crate_deposit": Decimal("5.00"), "bottle_deposit": Decimal("0.0")},
            {"name": "Water Bottle", "crate_price": Decimal("0.0"), "bottle_price": Decimal("0.80"), "crate_deposit": Decimal("0.0"), "bottle_deposit": Decimal("0.15")},
            {"name": "Juice Bottle", "crate_price": Decimal("0.0"), "bottle_price": Decimal("1.50"), "crate_deposit": Decimal("0.0"), "bottle_deposit": Decimal("0.25")},
        ]
        # With the codec, insert_many should handle the Decimals correctly
        db.products.insert_many(products_data)
        print(f"Inserted {len(products_data)} products.")

    # Seed Empties
    if db.empties.count_documents({}) == 0:
        print("Seeding empties...")
        empties_data = [
            {"name": "Empty Crate", "deposit_value": Decimal("5.00")},
            {"name": "Empty Bottle (Std)", "deposit_value": Decimal("0.15")},
            {"name": "Empty Bottle (Juice)", "deposit_value": Decimal("0.25")},
        ]
        db.empties.insert_many(empties_data)
        print(f"Inserted {len(empties_data)} empties.")

    # Ensure initial cash value exists (already handled in get_database)
    # But we can double-check and log here
    cash_doc = db.values.find_one({"label": "cash_on_hand"})
    if cash_doc is None:
         print("Re-initializing cash on hand during seeding...") # Should not happen if get_db worked
         db.values.update_one(
            {"label": "cash_on_hand"},
            {"$set": {"value": Decimal("100.00")}}, # Use Decimal
            upsert=True
        )
    else:
        print("Cash on hand already initialized.")


if __name__ == '__main__':
    # Test connection and seeding when running this file directly
    print("Testing database connection and seeding...")
    db_conn = get_database() # Ensure connection and codec are set up
    if db_conn is not None:
        seed_database()
        print("\n--- Current State ---")
        print("Products:")
        for p in get_products():
            # Display with formatting, ensuring they are Decimal
            print(f"  {p['name']}: Crate Price={p['crate_price']:.2f}, Bottle Price={p['bottle_price']:.2f}, Crate Deposit={p['crate_deposit']:.2f}, Bottle Deposit={p['bottle_deposit']:.2f}")

        print("\nEmpties:")
        for e in get_empties():
             print(f"  {e['name']}: Deposit={e['deposit_value']:.2f}")

        print(f"\nCash on Hand: {get_cash_on_hand():.2f}")

        # Test update
        print("\nTesting cash update...")
        initial_cash = get_cash_on_hand()
        if update_cash_on_hand(Decimal("10.55")):
            print(f"  Updated successfully. New cash: {get_cash_on_hand():.2f}")
        else:
            print("  Update failed.")
        # Test update back
        update_cash_on_hand(Decimal("-10.55")) # Restore original
        print(f"  Restored cash: {get_cash_on_hand():.2f}")

    else:
        print("\nDatabase connection failed. Cannot display data.")