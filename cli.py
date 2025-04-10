import sys
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Tuple

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
    ADDING_PRODUCT = 2
    ADDING_EMPTY = 3
    ADDING_QUANTITY = 4

# --- Application State ---
available_products_for_selection: list = [] # List of tuples (display_name, price_with_deposit) for sales
available_empties_for_selection: list = []  # List of tuples (display_name, credit_value) for returns
current_cart: dict = {}                     # List of dicts: {'name': str, 'price': Decimal}
status_message: str = ""
input_buffer: str = ""
input_mode: InputMode = InputMode.IDLE           # 'idle', 'adding_product_id', 'adding_empty_id', 'adding_quantity'
item_to_add: Tuple[str, dict] | None = None                    # Temp storage: {'name': str, 'price': Decimal}
quit_confirmation_pending: bool = False     # For quit confirmation

def reset_input_state():
    """Resets the multistep input process."""
    global input_buffer, input_mode, item_to_add, status_message
    input_buffer = ""
    input_mode = InputMode.IDLE
    item_to_add = None
    # Don't clear status_message here, it might hold important info (like "Input cancelled")

def populate_selection_lists():
    """Builds the separate lists for products and empties."""
    global available_products_for_selection, available_empties_for_selection
    available_products_for_selection = []
    available_empties_for_selection = []

    try:
        # Add items for sale (products)
        for product in db.get_products():
            # Crate Entry
            crate_price = product["crate_price"]
            crate_deposit = product["crate_deposit"]
            crate_total = crate_price + crate_deposit
            available_products_for_selection.append((f"{product['name']} Crate", crate_total))

            # Bottle Entry
            bottle_price = product["bottle_price"]
            bottle_deposit = product["bottle_deposit"]
            bottle_total = bottle_price + bottle_deposit
            available_products_for_selection.append((f"{product['name']} Bottle", bottle_total))

        # Add returnable empties (credit items)
        for empty in db.get_empties():
             available_empties_for_selection.append((empty["name"], empty["deposit_value"]))

    except KeyError as e:
        print(f"Error populating selection lists. An Database element is missing an required entry.\n{e}")
        sys.exit(1)

    # Sort lists
    available_products_for_selection.sort(key=lambda item: item[0])
    available_empties_for_selection.sort(key=lambda item: item[0])


# --- UI Content Functions ---

def _generate_item_list_text(title, items, item_type_char):
    """Helper to generate formatted text for a list of items."""
    lines = [HTML(f"<b><u><style fg='#FFD700'>{title} ({item_type_char} + #):</style></u></b>\n")]
    if not items:
        lines.append(HTML(" <style fg='#FFA07A'>No items configured.</style>\n"))
    else:
        for i, (name, price) in enumerate(items):
            price_str, style_tag = None, None
            if item_type_char == 'P':
                price_str = f"{price:.2f} EUR"
                style_tag = "fg='#90EE90'" # Light green for prices
            elif item_type_char == 'E':
                price_str = f"{price:.2f} EUR (Credit)"
                style_tag = "fg='#ADD8E6'" # Light blue for credit
            else:
                print(f"Error generating item list format text. Invalid item_type_char: {item_type_char}")
                sys.exit(1)

            lines.append(HTML(f" <style fg='white'>{i+1: >2}:</style> <style fg='#FFFFFF'>{name:<30}</style> <style {style_tag}>{price_str:>15}</style>\n"))

    return merge_formatted_text(lines)

def get_products_text():
    """Generates FormattedText for the available products list."""
    return _generate_item_list_text("Products for Sale", available_products_for_selection, "P")

def get_empties_text():
    """Generates FormattedText for the returnable empties list."""
    return _generate_item_list_text("Empties for Return", available_empties_for_selection, "E")


def get_cart_text():
    """Generates FormattedText for the shopping cart view. (Unchanged)"""
    lines = [HTML("<b><u><style fg='#FFD700'>Current Cart:</style></u></b>\n")] # Yellow title
    total = Decimal("0.00")

    if not current_cart:
        lines.append(HTML(" <style fg='#D3D3D3'>Cart is empty</style>\n")) # Light gray
    else:
        # Sort items alphabetically for consistent cart display
        sorted_item_names = sorted(current_cart.keys())

        for name in sorted_item_names:
            details = current_cart[name]
            quantity = details['quantity']
            price = details['price']
            item_total = price * quantity
            price_str = f"{price:.2f}"
            item_total_str = f"{item_total:.2f} EUR"

            # Determine style based on price
            price_style = "fg='#90EE90'" # Default green for positive prices
            if price < 0:
                price_style = "fg='#ADD8E6'" # Blue for credit items

            # Line 1: Quantity and Name
            lines.append(HTML(f" <style fg='white'>{quantity}x</style> <style fg='#FFFFFF'>{name:<30}</style>\n"))
            # Line 2: Indented price details
            lines.append(HTML(f"   <style fg='#A9A9A9'> (@ {price_str}) =</style> <style {price_style}>{item_total_str:>12}</style>\n")) # Dim gray label
            lines.append(HTML("\n")) # Add a blank line between items
            total += item_total # Add item total to grand total

    lines.append(HTML("<style fg='#808080'>------------------------------------</style>\n")) # Gray separator
    lines.append(HTML(f"<b><style fg='white'>Total:</style> <style fg='#90EE90'>{total:.2f} EUR</style></b>\n")) # White label, green total
    return merge_formatted_text(lines)


def get_status_toolbar_text():
    """Generates FormattedText for the bottom status/command bar."""
    global status_message, quit_confirmation_pending

    # Base commands - Hide Finish/Cancel/Undo during multistep input? Maybe not needed.
    commands = HTML(
        "<style bg='#2E8B57' fg='white'> " # SeaGreen background
        "[<b>P</b>]roduct | [<b>E</b>]mpty | [<b>F</b>]inish | [<b>C</b>]ancel | [<b>U</b>]ndo | [<b>Q</b>]uit "
        "</style>"
    )

    # Status/Input Prompt area
    status_content = ""
    status_style = "bg='#4682B4' fg='white'" # SteelBlue background default

    if quit_confirmation_pending:
        status_content = " Cart not empty. Press Q again to confirm quit, or any other key to cancel."
        status_style = "bg='#FFA500' fg='black'" # Orange background for warning
    elif input_mode == InputMode.ADDING_PRODUCT:
        status_content = f" Enter Product #: {input_buffer}"
    elif input_mode == InputMode.ADDING_EMPTY:
         status_content = f" Enter Empty #: {input_buffer}"
    elif input_mode == InputMode.ADDING_QUANTITY:
        item_name = item_to_add['name'] if item_to_add else 'Item'
        status_content = f" Enter Quantity for {item_name}: {input_buffer}"
    elif status_message:
        status_content = f" {status_message}"
        if "Error" in status_message or "Invalid" in status_message or "Cannot" in status_message:
            status_style = "bg='#DC143C' fg='white'" # Crimson background for errors
    else:
        status_content = f" Cash: {db.get_cash_on_hand():.2f} EUR" # Default status

    # Clear one-time status message after displaying it? Only if not in input mode.
    if input_mode == InputMode.IDLE and not quit_confirmation_pending:
       pass # Keep status_message until next action unless it's cleared explicitly

    status = HTML(f" <style {status_style}>{status_content:<60}</style>") # Pad for consistent width

    # Combine - only show commands and status bar for now. Input buffer integrated into status.
    full_text = merge_formatted_text([commands, status])

    return full_text


# --- Key Bindings ---
kb = KeyBindings()

def handle_action_interrupt():
    """Checks if an action interrupts quit confirmation or input mode."""
    global quit_confirmation_pending, status_message
    interrupted = False
    if quit_confirmation_pending:
        quit_confirmation_pending = False
        status_message = "Quit cancelled."
        interrupted = True
    if input_mode != InputMode.IDLE:
        reset_input_state()
        status_message = "Input cancelled." # Overwrites quit cancelled if both true
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
        status_message = "" # Clear previous status, confirmation message is handled by get_status_toolbar_text
    else:
        event.app.exit() # Exit directly if cart is empty


@kb.add('p')
def _(event):
    """ Start adding a product. """
    global input_mode, status_message
    if handle_action_interrupt(): return # Handle confirmations/cancellations

    if input_mode == InputMode.IDLE:
        status_message = "" # Clear previous status
        reset_input_state() # Clear buffer just in case
        input_mode = InputMode.ADDING_PRODUCT # Set mode *after* reset
    else:
        status_message = "Error: Already in input mode. Press Esc to cancel."

@kb.add('e')
def _(event):
    """ Start adding an empty/return. """
    global input_mode, status_message
    if handle_action_interrupt(): return # Handle confirmations/cancellations

    if input_mode == InputMode.IDLE:
        status_message = "" # Clear previous status
        reset_input_state() # Clear buffer just in case
        input_mode = InputMode.ADDING_EMPTY # Set mode *after* reset
    else:
        status_message = "Error: Already in input mode. Press Esc to cancel."


@kb.add('f')
def _(event):
    """ Finish Transaction """
    global current_cart, status_message, input_buffer, quit_confirmation_pending
    if handle_action_interrupt(): return # Handle confirmations/cancellations

    if not current_cart:
        status_message = "Cart is empty. Nothing to finish."
        return

    total = sum(item['price'] for item in current_cart.values())
    timestamp = datetime.now().isoformat()

    transaction = {
        "timestamp": timestamp,
        "total": str(total), # Convert Decimal to string
        "items": current_cart
    }

    try:
        db.add_transaction(transaction)
        db.update_cash_on_hand(Decimal(total))
        cash_on_hand = db.get_cash_on_hand() # Fetch updated value
        status_message = f"Transaction finished. Total: {total:.2f} EUR. Cash: {cash_on_hand:.2f} EUR."
        current_cart = {}
    except Exception as e:
        status_message = f"Error saving transaction: {e}"


@kb.add('c')
def _(event):
    """ Cancel Transaction (Clear Cart) """
    global current_cart, status_message
    if handle_action_interrupt(): return # Handle confirmations/cancellations

    if not current_cart:
        status_message = "Cart is already empty."
    else:
        current_cart = {}
        status_message = "Transaction cancelled. Cart cleared."


@kb.add('escape')
def _(event):
    """ Cancel current input operation """
    global status_message
    if input_mode != InputMode.IDLE:
        reset_input_state()
        status_message = "Input cancelled."
    elif quit_confirmation_pending:
         handle_action_interrupt() # Will reset pending flag and set message
    else:
        status_message = ""


@kb.add('backspace')
def _(event):
    """ Handle backspace during input """
    global input_buffer, status_message
    # Allow backspace only when in an input mode and buffer is not empty
    if input_mode in [InputMode.ADDING_EMPTY, InputMode.ADDING_PRODUCT, InputMode.ADDING_QUANTITY] and input_buffer:
        input_buffer = input_buffer[:-1]
        status_message = "" # Clear any previous error message
    elif quit_confirmation_pending:
         handle_action_interrupt() # Treat as cancelling quit confirmation
    else:
        # Optional: Provide feedback if backspace is pressed inappropriately
        # status_message = "Cannot Backspace."
        pass # Ignore


@kb.add('enter')
def _(event):
    """ Process entered number (ID or Quantity) """
    global input_buffer, status_message, input_mode, item_to_add, current_cart

    if quit_confirmation_pending:
        handle_action_interrupt() # Treat Enter as cancelling quit confirmation
        return

    if input_mode == InputMode.ADDING_PRODUCT:
        try:
            if not input_buffer:
                status_message = "Error: No product number entered."
                return
            num = int(input_buffer)
            if 1 <= num <= len(available_products_for_selection):
                item_index = num - 1
                name, price = available_products_for_selection[item_index]
                item_to_add = (name, {'price': price})
                input_mode = InputMode.ADDING_QUANTITY
                input_buffer = ""
                status_message = "" # Clear previous status
            else:
                status_message = f"Error: Invalid product number: {num}"
                input_buffer = "" # Clear invalid input
        except ValueError:
            status_message = f"Error: Invalid input: '{input_buffer}'"
            input_buffer = "" # Clear invalid input

    elif input_mode == InputMode.ADDING_EMPTY:
        try:
            if not input_buffer:
                status_message = "Error: No empty number entered."
                return
            num = int(input_buffer)
            if 1 <= num <= len(available_empties_for_selection):
                item_index = num - 1
                name, price = available_empties_for_selection[item_index]
                item_to_add = (name, {'price': price})
                input_mode = InputMode.ADDING_QUANTITY
                input_buffer = ""
                status_message = "" # Clear previous status
            else:
                status_message = f"Error: Invalid empty number: {num}"
                input_buffer = "" # Clear invalid input
        except ValueError:
            status_message = f"Error: Invalid input: '{input_buffer}'"
            input_buffer = "" # Clear invalid input

    elif input_mode == InputMode.ADDING_QUANTITY:
        try:
            if not input_buffer:
                 status_message = "Error: No quantity entered."
                 return
            quantity = int(input_buffer)
            if quantity > 0:
                if item_to_add:
                    # check if the product already is in the cart, if yes add the quantity
                    if item_to_add[0] in current_cart.keys():
                        # item already exists
                        assert current_cart[item_to_add[0]]["price"] == item_to_add[1]["price"]
                        current_cart[item_to_add[0]]["quantity"] += item_to_add[1]["quantity"]
                    else:
                        # add the new item
                        current_cart[item_to_add[0]] = item_to_add[1] # Add copies
                    status_message = f"Added {quantity}x {item_to_add[0]}"
                    reset_input_state() # Back to idle
                else:
                     status_message = "Error: No item selected to add quantity for." # Should not happen
                     reset_input_state()
            else:
                status_message = "Error: Quantity must be positive."
                input_buffer = "" # Clear invalid input
        except ValueError:
            status_message = f"Error: Invalid quantity: '{input_buffer}'"
            input_buffer = "" # Clear invalid input

    # Ignore Enter if in 'idle' mode


@kb.add('<any>')
def _(event):
    """ Handle digit input during appropriate modes """
    global input_buffer, status_message

    # Always handle quit confirmation cancellation first
    if quit_confirmation_pending:
        # Any key other than 'q' cancels the confirmation
        if event.key_sequence[0].data.lower() != 'q':
             handle_action_interrupt()
             # Maybe process the key press normally now? Depends on desired behavior.
             # For simplicity, let's just cancel and require re-entry of the command.
             return

    char = event.key_sequence[0].data
    if char.isdigit():
        if input_mode in [InputMode.ADDING_EMPTY, InputMode.ADDING_PRODUCT, InputMode.ADDING_QUANTITY]:
            input_buffer += char
            status_message = "" # Clear previous status/error messages
        # Ignore digits if in 'idle' mode
    elif char.isalpha() and char.lower() not in 'pefcq':
        # Handle unknown alphabetic keys only when idle
         if input_mode == InputMode.IDLE:
             status_message = f"Unknown command: {char}"
             reset_input_state() # Ensure buffer/mode are clear
        # else: Ignore unknown alpha keys during input


# --- Layout Definition ---
style = Style.from_dict({
    '': 'fg:white bg:',
    'window.border': 'fg:#888888',
    'separator': 'fg:#606060',
    # Status bar styles are handled inline via HTML
})

# Left Pane Top: Products
products_window = Window(
    content=FormattedTextControl(get_products_text, focusable=False),
    style="class:window.border" # Optional border
)

# Left Pane Bottom: Empties
empties_window = Window(
    content=FormattedTextControl(get_empties_text, focusable=False),
    style="class:window.border" # Optional border
)

# Right Pane: Shopping Cart
cart_window = Window(
    content=FormattedTextControl(get_cart_text, focusable=False),
    align=WindowAlign.LEFT
)

# Bottom Toolbar: Status and Commands
status_toolbar = Window(
    height=1,
    content=FormattedTextControl(get_status_toolbar_text, focusable=False),
    style="class:status"
)

# Main Layout Container
root_container = HSplit([
    # Top part: VSplit for Left (Products/Empties) and Right (Cart)
    VSplit([
        # Left side: HSplit for Products and Empties
        HSplit([
            products_window,
            Window(height=1, char='─', style='class:separator'), # Horizontal separator
            empties_window
        ], height=None), # Let the VSplit manage height distribution

        # Vertical separator
        Window(width=1, char='│', style='class:separator'),

        # Right side: Cart
        cart_window,
    ], padding=1, style='bg:#1c1c1c'), # Padding around the main content area

    # Bottom part: Status bar
    status_toolbar,
])

layout = Layout(root_container)


# --- Main Application Execution ---
def main():
    global status_message

    # 1. Populate the selection lists from loaded data
    try:
        populate_selection_lists()
    except Exception as e:
         print(f"Error populating item lists: {e}", file=sys.stderr)
         sys.exit(1)


    # 2. Initial status
    try:
        cash_on_hand = db.get_cash_on_hand()
        status_message = f"Store Ready. Cash: {cash_on_hand:.2f} EUR"
    except Exception as e:
        print(f"Error getting initial cash on hand: {e}", file=sys.stderr)
        sys.exit(1)


    # 3. Create and Run Application
    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        style=style,
        mouse_support=False
    )
    print("Starting Beverage Store CLI... (Press Q to quit)")
    try:
        app.run()
    finally:
        print("Application exited.")


if __name__ == "__main__":
    main()