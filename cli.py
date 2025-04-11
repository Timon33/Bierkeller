import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Tuple, List, Dict, Any

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.formatted_text import merge_formatted_text, HTML
from prompt_toolkit.styles import Style

import database as db


class InputMode(Enum):
    IDLE = 1
    ADDING_CRATE = 2
    ADDING_BOTTLE = 3
    ADDING_EMPTY = 4
    ADDING_QUANTITY = 5
    REMOVING_ITEM = 6

# --- Application State ---
# Type hint for clarity
CartItemDetails = Dict[str, Any] # Keys: 'quantity', 'base_price', 'deposit', 'total_price'
ItemToAddPayload = Dict[str, Decimal] # Keys: 'base_price', 'deposit', 'total_price'

available_crates_for_selection: List[Tuple[str, Decimal, Decimal, Decimal]] = [] # (display_name, base_price, deposit, total_price)
available_bottles_for_selection: List[Tuple[str, Decimal, Decimal, Decimal]] = [] # (display_name, base_price, deposit, total_price)
available_empties_for_selection: List[Tuple[str, Decimal]] = []  # (display_name, credit_value)
current_cart: Dict[str, CartItemDetails] = {} # Key: unique item name, Value: CartItemDetails
status_message: str = ""
input_buffer: str = ""
input_mode: InputMode = InputMode.IDLE
# Temp storage: (unique_name, {'base_price': Decimal, 'deposit': Decimal, 'total_price': Decimal})
item_to_add: Tuple[str, ItemToAddPayload] | None = None
quit_confirmation_pending: bool = False
# Helper to map displayed cart index to item name for removal
cart_display_order: List[str] = []


def reset_input_state():
    """Resets the multistep input process."""
    global input_buffer, input_mode, item_to_add, status_message
    input_buffer = ""
    input_mode = InputMode.IDLE
    item_to_add = None
    # Don't clear status_message here, it might hold important info

def quantize_decimal(value: Decimal) -> Decimal:
    """Ensure consistent two-decimal place formatting."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def populate_selection_lists():
    """Builds the separate lists for crates, bottles, and empties."""
    global available_crates_for_selection, available_bottles_for_selection, available_empties_for_selection
    available_crates_for_selection = []
    available_bottles_for_selection = []
    available_empties_for_selection = []

    try:
        # Add Crates
        for product in db.get_products():
            name = product['name']
            crate_price = quantize_decimal(product["crate_price"])
            crate_deposit = quantize_decimal(product["crate_deposit"])
            crate_total = crate_price + crate_deposit
            available_crates_for_selection.append((f"{name} Crate", crate_price, crate_deposit, crate_total))

        # Add Bottles
        for product in db.get_products():
            name = product['name']
            bottle_price = quantize_decimal(product["bottle_price"])
            bottle_deposit = quantize_decimal(product["bottle_deposit"])
            bottle_total = bottle_price + bottle_deposit
            available_bottles_for_selection.append((f"{name} Bottle", bottle_price, bottle_deposit, bottle_total))

        # Add returnable empties (credit items)
        for empty in db.get_empties():
             # Store deposit as negative for calculations, display as positive credit
             credit_value = quantize_decimal(empty["deposit_value"])
             available_empties_for_selection.append((empty["name"], -credit_value)) # Store as negative

    except KeyError as e:
        print(f"Error populating selection lists. A Database element is missing a required entry: {e}", file=sys.stderr)
        sys.exit(1)
    except TypeError as e:
        print(f"Error populating selection lists. Check data types in database (expecting Decimals?): {e}", file=sys.stderr)
        sys.exit(1)


    # Sort lists alphabetically by name
    available_crates_for_selection.sort(key=lambda item: item[0])
    available_bottles_for_selection.sort(key=lambda item: item[0])
    available_empties_for_selection.sort(key=lambda item: item[0])


# --- UI Content Functions ---

def _generate_item_list_text(title, items, item_type_char):
    """Helper to generate formatted text for a list of items (crates, bottles, empties)."""
    lines = [HTML(f"<b><u><style fg='#FFD700'>{title} ({item_type_char} + #):</style></u></b>\n")]
    if not items:
        lines.append(HTML(" <style fg='#FFA07A'>No items configured.</style>\n"))
    else:
        for i, item_data in enumerate(items):
            name = item_data[0]
            price_str = ""
            style_tag = "fg='#FFFFFF'" # Default white

            if item_type_char in ('A', 'B'): # Crates (A) or Bottles (B)
                base_price, deposit, total_price = item_data[1:]
                price_str = f"({base_price:.2f} + {deposit:.2f}) {total_price:.2f} EUR"
                style_tag = "fg='#90EE90'" # Light green for prices
            elif item_type_char == 'E': # Empties
                credit_value = abs(item_data[1]) # Display positive credit
                price_str = f"{credit_value:.2f} EUR (Credit)"
                style_tag = "fg='#ADD8E6'" # Light blue for credit
            else:
                # Should not happen
                print(f"Error generating item list format text. Invalid item_type_char: {item_type_char}")
                return HTML("<style bg='red' fg='white'>Internal Error</style>")

            lines.append(HTML(f" <style fg='white'>{i+1: >2}:</style> <style fg='#FFFFFF'>{name:<30}</style> <style {style_tag}>{price_str:>30}</style>\n"))

    return merge_formatted_text(lines)

def get_crates_text():
    """Generates FormattedText for the available crates list."""
    return _generate_item_list_text("Crates for Sale", available_crates_for_selection, "A") # 'A' for Add Crate

def get_bottles_text():
    """Generates FormattedText for the available bottles list."""
    return _generate_item_list_text("Bottles for Sale", available_bottles_for_selection, "B") # 'B' for Add Bottle

def get_empties_text():
    """Generates FormattedText for the returnable empties list."""
    # Pass negative credit value, helper handles display
    return _generate_item_list_text("Empties for Return", available_empties_for_selection, "E")

def get_cart_text():
    """Generates FormattedText for the shopping cart view."""
    global cart_display_order # We need to update this list here
    lines = [HTML("<b><u><style fg='#FFD700'>Current Cart:</style></u></b>\n")] # Yellow title
    total = Decimal("0.00")
    cart_display_order = [] # Reset display order
    target_content_width = 50

    if not current_cart:
        lines.append(HTML(" <style fg='#D3D3D3'>Cart is empty</style>\n")) # Light gray
    else:
        # Sort items alphabetically for consistent cart display AND indexing
        sorted_item_names = sorted(current_cart.keys())
        cart_display_order = sorted_item_names # Store the order for removal

        for i, name in enumerate(sorted_item_names):
            details = current_cart[name]
            quantity = details['quantity']
            base_price = details['base_price']
            deposit = details['deposit']
            total_price_per_item = details['total_price'] # Price per single item (base+deposit) or credit
            item_line_total = total_price_per_item * quantity

            # Determine style based on price (credit items have negative total_price_per_item)
            price_style = "fg='#90EE90'" # Default green for positive prices
            if total_price_per_item < 0:
                price_style = "fg='#ADD8E6'" # Blue for credit items

            # Line 1: Index, Quantity and Name
            lines.append(HTML(f"<style fg='cyan'>{i+1: >2}:</style> <style fg='white'>{quantity}x</style> <style fg='#FFFFFF'>{name:<30}</style>\n"))

            indent = "     "  # 5 spaces

            # Calculate price_details_str
            if total_price_per_item < 0:
                price_details_str = f"(Credit @ {abs(total_price_per_item):.2f})"
            else:
                price_details_str = f"(@ {base_price:.2f} + {deposit:.2f} Pf = {total_price_per_item:.2f})"

            # Format the final total string for this line
            total_str = f"{item_line_total:.2f} EUR"

            # Calculate visible lengths (approximate, ignoring HTML tags)
            len_part1 = len(f"{indent}{price_details_str} =")
            len_part2 = len(total_str)

            padding_needed = target_content_width - len_part1 - len_part2 - 1
            padding_spaces = " " * max(1, padding_needed)  # Ensure at least one space

            # --- Construct the final HTML line ---
            lines.append(HTML(
                f"{indent}<style fg='#A9A9A9'>{price_details_str} =</style>"  # Part 1 styled
                f"{padding_spaces}"  # Dynamic padding
                f"<style {price_style}>{total_str}</style>"  # Part 2 styled
                "\n"
            ))

            lines.append(HTML("\n")) # Add a blank line between items
            total += item_line_total # Add item line total to grand total

    lines.append(HTML("<style fg='#808080'>-----------------------------------------</style>\n")) # Gray separator
    total_display_style = "fg='#90EE90'" if total >= 0 else "fg='#ADD8E6'"
    lines.append(HTML(f"<b><style fg='white'>Total:</style> <style {total_display_style}>{total:.2f} EUR</style></b>\n")) # White label, green/blue total
    return merge_formatted_text(lines)


def get_status_toolbar_text():
    """Generates FormattedText for the bottom status/command bar."""
    global status_message, quit_confirmation_pending

    # Updated commands
    commands = HTML(
        "<style bg='#2E8B57' fg='white'> " # SeaGreen background
        "[<b>A</b>] Crate | [<b>B</b>] Bottle | [<b>E</b>]mpty | [<b>R</b>]emove | [<b>F</b>]inish | [<b>C</b>]ancel | [<b>Q</b>]uit " 
        "</style>"
    )

    # Status/Input Prompt area
    status_content = ""
    status_style = "bg='#4682B4' fg='white'" # SteelBlue background default

    if quit_confirmation_pending:
        status_content = " Cart not empty. Press Q again to confirm quit, or any other key to cancel."
        status_style = "bg='#FFA500' fg='black'" # Orange background for warning
    elif input_mode == InputMode.ADDING_CRATE:
        status_content = f" Enter Crate #: {input_buffer}"
    elif input_mode == InputMode.ADDING_BOTTLE:
        status_content = f" Enter Bottle #: {input_buffer}"
    elif input_mode == InputMode.ADDING_EMPTY:
         status_content = f" Enter Empty #: {input_buffer}"
    elif input_mode == InputMode.ADDING_QUANTITY:
        item_name = item_to_add[0] if item_to_add else 'Item'
        status_content = f" Enter Quantity for {item_name}: {input_buffer}"
    elif input_mode == InputMode.REMOVING_ITEM:
         status_content = f" Enter Cart Item # to Remove: {input_buffer}"
    elif status_message:
        status_content = f" {status_message}"
        if "Error" in status_message or "Invalid" in status_message or "Cannot" in status_message:
            status_style = "bg='#DC143C' fg='white'" # Crimson background for errors
    else:
        # Show cash only when idle and no other message is active
        status_content = f" Cash: {db.get_cash_on_hand():.2f} EUR"

    # Clear one-time status message after displaying it only if idle and not confirming quit
    if input_mode == InputMode.IDLE and not quit_confirmation_pending:
        # status_message = "" # Decided against auto-clearing, let actions clear it explicitly
        pass

    status = HTML(f" <style {status_style}>{status_content:<65}</style>") # Pad for consistent width

    full_text = merge_formatted_text([commands, status])
    return full_text


# --- Key Bindings ---
kb = KeyBindings()

def handle_action_interrupt():
    """Checks if an action interrupts quit confirmation or input mode. Returns True if interrupted."""
    global quit_confirmation_pending, status_message
    interrupted = False
    if quit_confirmation_pending:
        quit_confirmation_pending = False
        status_message = "Quit cancelled."
        interrupted = True
    # Check if *any* input mode is active
    if input_mode != InputMode.IDLE:
        current_mode = input_mode # Store before reset
        reset_input_state()
        # Don't overwrite "Quit cancelled." if that happened first
        if not interrupted:
             status_message = "Input cancelled."
        # If we were adding quantity, item_to_add is cleared by reset, which is correct.
        interrupted = True
    return interrupted

@kb.add('q')
def _(event):
    """ Quit application, potentially with confirmation. """
    global quit_confirmation_pending, status_message

    if quit_confirmation_pending:
        event.app.exit()
        return

    # If in middle of input, cancel input first
    if input_mode != InputMode.IDLE:
        reset_input_state()
        status_message = "Input cancelled. Press Q again to quit."
        return # Require another Q

    if current_cart:
        quit_confirmation_pending = True
        status_message = "" # Clear previous status, confirmation message is handled by toolbar
    else:
        event.app.exit() # Exit directly if cart is empty


@kb.add('a') # Add Crate
def _(event):
    """ Start adding a crate. """
    global input_mode, status_message
    if handle_action_interrupt(): return

    if input_mode == InputMode.IDLE:
        status_message = ""
        reset_input_state()
        input_mode = InputMode.ADDING_CRATE
    else:
        # This case should ideally not be reachable due to handle_action_interrupt
        status_message = "Error: Already in input mode. Press Esc to cancel."

@kb.add('b') # Add Bottle
def _(event):
    """ Start adding a bottle. """
    global input_mode, status_message
    if handle_action_interrupt(): return

    if input_mode == InputMode.IDLE:
        status_message = ""
        reset_input_state()
        input_mode = InputMode.ADDING_BOTTLE
    else:
        status_message = "Error: Already in input mode. Press Esc to cancel."

@kb.add('e') # Add Empty
def _(event):
    """ Start adding an empty/return. """
    global input_mode, status_message
    if handle_action_interrupt(): return

    if input_mode == InputMode.IDLE:
        status_message = ""
        reset_input_state()
        input_mode = InputMode.ADDING_EMPTY
    else:
        status_message = "Error: Already in input mode. Press Esc to cancel."

@kb.add('r') # Remove Item
def _(event):
    """ Start removing an item from the cart. """
    global input_mode, status_message
    if handle_action_interrupt(): return

    if not current_cart:
        status_message = "Cart is empty. Nothing to remove."
        return

    if input_mode == InputMode.IDLE:
        status_message = ""
        reset_input_state()
        input_mode = InputMode.REMOVING_ITEM
    else:
        status_message = "Error: Already in input mode. Press Esc to cancel."


@kb.add('f')
def _(event):
    """ Finish Transaction """
    global current_cart, status_message, input_buffer, quit_confirmation_pending
    if handle_action_interrupt(): return

    if not current_cart:
        status_message = "Cart is empty. Nothing to finish."
        return

    # Calculate total based on stored total_price per item * quantity
    total = Decimal("0.00")
    for item_details in current_cart.values():
        total += item_details['total_price'] * item_details['quantity']
    total = quantize_decimal(total)

    timestamp = datetime.now().isoformat()

    # Prepare items for transaction log (optional: simplify structure?)
    logged_items = {
        name: {
            'quantity': details['quantity'],
            'base_price': str(details['base_price']),
            'deposit': str(details['deposit']),
            'total_price_per_item': str(details['total_price']),
            'line_total': str(quantize_decimal(details['total_price'] * details['quantity']))
        } for name, details in current_cart.items()
    }

    transaction = {
        "timestamp": timestamp,
        "total": str(total), # Convert Decimal to string for storage
        "items": logged_items
    }

    try:
        if not db.add_transaction(transaction):
            raise Exception("Transaction could not be saved to database")
        if not db.update_cash_on_hand(total): # Use the calculated Decimal total
            raise Exception("Cash could not be updated")
        cash_on_hand = db.get_cash_on_hand()
        status_message = f"Transaction finished. Total: {total:.2f} EUR. Cash: {cash_on_hand:.2f} EUR."
        current_cart = {}
        cart_display_order.clear() # Clear display order as cart is empty
        reset_input_state() # Go back to idle
    except Exception as e:
        status_message = f"Error saving transaction: {e}"
        # Optionally reset state here too? Depends on desired error recovery.
        # reset_input_state()


@kb.add('c')
def _(event):
    """ Cancel Transaction (Clear Cart) """
    global current_cart, status_message, cart_display_order
    if handle_action_interrupt(): return

    if not current_cart:
        status_message = "Cart is already empty."
    else:
        current_cart = {}
        cart_display_order.clear() # Clear display order
        status_message = "Transaction cancelled. Cart cleared."
        reset_input_state() # Ensure idle state


@kb.add('escape')
def _(event):
    """ Cancel current input operation or quit confirmation """
    global status_message
    if input_mode != InputMode.IDLE:
        reset_input_state()
        status_message = "Input cancelled."
    elif quit_confirmation_pending:
         handle_action_interrupt() # Will reset pending flag and set message
    else:
        # Optional: Clear status if Esc is pressed when idle
        status_message = ""


@kb.add('backspace')
def _(event):
    """ Handle backspace during input """
    global input_buffer, status_message
    if input_mode != InputMode.IDLE and input_buffer:
        input_buffer = input_buffer[:-1]
        status_message = "" # Clear any previous error message
    elif quit_confirmation_pending:
         handle_action_interrupt() # Treat as cancelling quit confirmation
    # else: Ignore backspace if not in input mode or buffer is empty


@kb.add('enter')
def _(event):
    """ Process entered number (ID, Quantity, or Remove Index) """
    global input_buffer, status_message, input_mode, item_to_add, current_cart, cart_display_order

    if quit_confirmation_pending:
        handle_action_interrupt()
        return

    if not input_buffer and input_mode != InputMode.IDLE:
        status_message = "Error: No number entered."
        # Don't clear buffer here, user might want to type something
        return

    # --- Handle ID Inputs ---
    if input_mode == InputMode.ADDING_CRATE:
        try:
            num = int(input_buffer)
            if 1 <= num <= len(available_crates_for_selection):
                item_index = num - 1
                name, base_price, deposit, total_price = available_crates_for_selection[item_index]
                item_to_add = (name, {'base_price': base_price, 'deposit': deposit, 'total_price': total_price})
                input_mode = InputMode.ADDING_QUANTITY
                input_buffer = ""
                status_message = ""
            else:
                status_message = f"Error: Invalid crate number: {num}"
                input_buffer = ""
        except ValueError:
            status_message = f"Error: Invalid input: '{input_buffer}'"
            input_buffer = ""

    elif input_mode == InputMode.ADDING_BOTTLE:
        try:
            num = int(input_buffer)
            if 1 <= num <= len(available_bottles_for_selection):
                item_index = num - 1
                name, base_price, deposit, total_price = available_bottles_for_selection[item_index]
                item_to_add = (name, {'base_price': base_price, 'deposit': deposit, 'total_price': total_price})
                input_mode = InputMode.ADDING_QUANTITY
                input_buffer = ""
                status_message = ""
            else:
                status_message = f"Error: Invalid bottle number: {num}"
                input_buffer = ""
        except ValueError:
            status_message = f"Error: Invalid input: '{input_buffer}'"
            input_buffer = ""

    elif input_mode == InputMode.ADDING_EMPTY:
        try:
            num = int(input_buffer)
            if 1 <= num <= len(available_empties_for_selection):
                item_index = num - 1
                name, credit_value = available_empties_for_selection[item_index] # credit_value is negative
                # Empties have no base price/deposit, just a total credit value
                item_to_add = (name, {'base_price': Decimal('0.00'), 'deposit': Decimal('0.00'), 'total_price': credit_value})
                input_mode = InputMode.ADDING_QUANTITY
                input_buffer = ""
                status_message = ""
            else:
                status_message = f"Error: Invalid empty number: {num}"
                input_buffer = ""
        except ValueError:
            status_message = f"Error: Invalid input: '{input_buffer}'"
            input_buffer = ""

    # --- Handle Quantity Input ---
    elif input_mode == InputMode.ADDING_QUANTITY:
        try:
            quantity = int(input_buffer)
            if quantity > 0:
                if item_to_add:
                    name, details_to_add = item_to_add
                    base_price = details_to_add['base_price']
                    deposit = details_to_add['deposit']
                    total_price = details_to_add['total_price']

                    if name in current_cart:
                        # Item already exists, just add quantity
                        # Assert that prices match (sanity check)
                        # Note: Floating point comparisons can be tricky, but Decimal should be exact here
                        assert current_cart[name]["total_price"] == total_price, f"Price mismatch for {name}!"
                        current_cart[name]["quantity"] += quantity
                    else:
                        # Add the new item with all details
                        current_cart[name] = {
                            'quantity': quantity,
                            'base_price': base_price,
                            'deposit': deposit,
                            'total_price': total_price
                        }
                    status_message = f"Added {quantity}x {name}"
                    reset_input_state() # Back to idle
                else:
                     status_message = "Error: No item selected to add quantity for (Internal Error)."
                     reset_input_state()
            else:
                status_message = "Error: Quantity must be positive."
                input_buffer = "" # Clear invalid input
        except ValueError:
            status_message = f"Error: Invalid quantity: '{input_buffer}'"
            input_buffer = ""

    # --- Handle Remove Item Input ---
    elif input_mode == InputMode.REMOVING_ITEM:
        try:
            num = int(input_buffer)
            # cart_display_order holds the names in the order they were displayed
            if 1 <= num <= len(cart_display_order):
                item_index_to_remove = num - 1
                item_name_to_remove = cart_display_order[item_index_to_remove]

                if item_name_to_remove in current_cart:
                    del current_cart[item_name_to_remove]
                    status_message = f"Removed item #{num}: {item_name_to_remove}"
                    # cart_display_order will be rebuilt on next cart render
                    reset_input_state() # Back to idle
                else:
                    # This should not happen if cart_display_order is correct
                    status_message = f"Error: Item '{item_name_to_remove}' not found in cart (Internal Error)."
                    reset_input_state()
            else:
                status_message = f"Error: Invalid cart item number: {num}"
                input_buffer = "" # Clear invalid input
        except ValueError:
            status_message = f"Error: Invalid input: '{input_buffer}'"
            input_buffer = ""
        except IndexError:
             status_message = f"Error: Could not find item at index {input_buffer} (Internal Error)."
             reset_input_state()

    # Ignore Enter if in 'idle' mode


@kb.add('<any>')
def _(event):
    """ Handle digit input during appropriate modes or cancel quit confirmation """
    global input_buffer, status_message

    # Always handle quit confirmation cancellation first
    if quit_confirmation_pending:
        if event.key_sequence[0].data.lower() != 'q':
             handle_action_interrupt()
             # Don't process the key further after cancelling quit, require re-entry.
             return

    char = event.key_sequence[0].data
    if char.isdigit():
        # Allow digits only in specific input modes
        if input_mode in [InputMode.ADDING_CRATE, InputMode.ADDING_BOTTLE, InputMode.ADDING_EMPTY, InputMode.ADDING_QUANTITY, InputMode.REMOVING_ITEM]:
            input_buffer += char
            status_message = "" # Clear previous status/error messages
        # Ignore digits if in 'idle' mode or other modes
    elif char.isalpha():
        # Handle known command keys (handled by their specific decorators 'a', 'b', 'e', 'r', 'f', 'c', 'q')
        # Handle unknown alphabetic keys only when idle
        known_commands = 'abefcrq' # Case-insensitive check below
        if input_mode == InputMode.IDLE and char.lower() not in known_commands:
             status_message = f"Unknown command: {char}"
             # reset_input_state() # Should already be idle, no buffer to clear
        # Ignore unknown alpha keys during input modes


# --- Layout Definition ---
style = Style.from_dict({
    # '': 'fg:white bg:#1c1c1c', # Base style - applied to root VSplit instead
    'window.border': 'fg:#888888',
    'separator': 'fg:#606060',
    # Status bar styles are handled inline via HTML
})

# Left Pane Top: Crates
crates_window = Window(
    content=FormattedTextControl(get_crates_text, focusable=False),
    style="class:window.border"
)

# Left Pane Middle: Bottles
bottles_window = Window(
    content=FormattedTextControl(get_bottles_text, focusable=False),
    style="class:window.border"
)

# Left Pane Bottom: Empties
empties_window = Window(
    content=FormattedTextControl(get_empties_text, focusable=False),
    style="class:window.border"
)

# Right Pane: Shopping Cart
cart_window = Window(
    content=FormattedTextControl(get_cart_text, focusable=False),
    align=WindowAlign.LEFT # Keep alignment
)

# Bottom Toolbar: Status and Commands
status_toolbar = Window(
    height=1,
    content=FormattedTextControl(get_status_toolbar_text, focusable=False),
    # style="class:status" # Styling is now inline HTML
)

# Main Layout Container
root_container = HSplit([
    # Top part: VSplit for Left (Crates/Bottles/Empties) and Right (Cart)
    VSplit([
        # Left side: HSplit for Crates, Bottles, Empties
        HSplit([
            crates_window,
            Window(height=1, char='─', style='class:separator'), # Horizontal separator
            bottles_window,
            Window(height=1, char='─', style='class:separator'), # Horizontal separator
            empties_window
        ], padding=0), # Let VSplit handle padding, internal elements share height

        # Vertical separator
        Window(width=1, char='│', style='class:separator'),

        # Right side: Cart
        cart_window,
    ], padding=1, style='bg:#1c1c1c'), # Padding around the main content area, dark background

    # Bottom part: Status bar
    status_toolbar,
])

layout = Layout(root_container)


# --- Main Application Execution ---
def main():
    global status_message

    print("Initializing Beverage Store CLI...")

    # 1. Load database and Populate the selection lists
    try:
        # Ensure database connection/loading happens if needed (assuming db module handles it)
        print("Loading data...")
        populate_selection_lists()
        print("Data loaded.")
    except Exception as e:
         print(f"FATAL: Error populating item lists during startup: {e}", file=sys.stderr)
         sys.exit(1)

    # 2. Initial status
    try:
        cash_on_hand = db.get_cash_on_hand()
        status_message = f"Store Ready. Cash: {cash_on_hand:.2f} EUR"
    except Exception as e:
        # Non-fatal? Or should it exit? Let's make it fatal for consistency.
        print(f"FATAL: Error getting initial cash on hand: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Create and Run Application
    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        style=style,
        mouse_support=False # Keep mouse disabled unless needed
    )
    print("Starting Application... (Press Q to quit)")
    try:
        app.run()
    except Exception as e:
        # Catch unexpected errors during run
        print("\n--- UNEXPECTED APPLICATION ERROR ---", file=sys.stderr)
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        print("------------------------------------", file=sys.stderr)
    finally:
        print("Application exited.")


if __name__ == "__main__":
    # Add basic check for database module existence early
    if 'db' not in globals():
        print("FATAL: Database module 'database.py' could not be imported.", file=sys.stderr)
        sys.exit(1)
    main()