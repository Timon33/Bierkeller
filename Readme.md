## Project Overview

This project implements a simple command-line interface (CLI) for a beverage store point-of-sale system. It allows users to add crates and bottles to a cart, return empties for credit, manage the cart, finalize transactions, and track cash on hand. The application uses MongoDB as its database and leverages Python's `Decimal` type for accurate monetary calculations.

## How to Run

### Prerequisites

1.  **Python 3:** Ensure you have Python 3 installed.
2.  **MongoDB:** A running MongoDB instance is required. The application connects to `mongodb://localhost:27017/` by default.
3.  **Python Libraries:** You need `pymongo` (which includes `bson`) and `prompt_toolkit`.

### Installation

1.  Clone or download the project files (`database.py`, `cli.py`).
2.  Install the required Python libraries:
    ```bash
    pip install pymongo prompt_toolkit
    ```
3.  Make sure your MongoDB server is running on `localhost:27017`.

### Execution

1.  Navigate to the directory containing the files in your terminal.
2.  Run the CLI application:
    ```bash
    python cli.py
    ```
3.  The application will start in full-screen mode. Follow the on-screen commands (e.g., `A` to add a crate, `B` for a bottle, `E` for empty returns, `R` to remove, `F` to finish, `C` to cancel, `Q` to quit).

## Inspect Database

Install MongoDB Compass and connect it to `localhost:27017/`. The database is called `beverage_store` and should have 4 collections (`empties`, `products`, `transactions`, `values`). You can use the gui to delete transactions, add new products, check the current amount of cash, etc.

## Code Overview

The project consists of two main files:

### `database.py`

*   **Purpose:** Handles all interactions with the MongoDB database.
*   **Key Features:**
    *   Establishes connection to the MongoDB server.
    *   Implements a **custom `DecimalCodec`** to automatically convert between Python's `Decimal` type and MongoDB's `Decimal128` BSON type. This ensures accurate storage and retrieval of monetary values.
    *   Provides functions to:
        *   `get_database()`: Connects and returns the database object with the codec enabled. Initializes cash on hand if needed.
        *   `get_products()`: Fetches product data (prices, deposits), ensuring values are `Decimal`.
        *   `get_empties()`: Fetches empty return data (deposit values), ensuring values are `Decimal`.
        *   `get_cash_on_hand()`: Retrieves the current cash balance as a `Decimal`.
        *   `update_cash_on_hand(Decimal)`: Atomically updates the cash balance using `$inc`.
        *   `add_transaction(dict)`: Stores transaction details.
        *   `seed_database()`: Populates the database with initial sample data if collections are empty (using `Decimal` values).

### `cli.py`

*   **Purpose:** Implements the user interface and application logic.
*   **Key Features:**
    *   Uses the `prompt_toolkit` library to create an interactive, full-screen terminal UI.
    *   Defines application state using an `InputMode` enum, `current_cart` dictionary, status messages, etc.
    *   Populates selection lists (`available_crates_for_selection`, etc.) by fetching data via `database.py`.
    *   Uses **Python `Decimal`** for all internal monetary calculations (totals, credits).
    *   Defines the layout (`HSplit`, `VSplit`, `Window`) to display item lists and the cart.
    *   Uses `FormattedTextControl` with HTML-like formatting for styled text output.
    *   Manages user input via `KeyBindings`:
        *   Handles adding items (crates, bottles, empties) through a multi-step process (select item type -> enter ID -> enter quantity).
        *   Allows removing items from the cart.
        *   Handles finishing a transaction (`F`), which calculates the total, updates cash on hand via `database.update_cash_on_hand`, logs the transaction via `database.add_transaction`, and clears the cart.
        *   Allows cancelling the current transaction/clearing the cart (`C`).
        *   Provides input cancellation (`Escape`) and quit functionality (`Q`, with confirmation if the cart is not empty).
    *   Displays status messages, input prompts, and current cash on hand in a bottom toolbar.