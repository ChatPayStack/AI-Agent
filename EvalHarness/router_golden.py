router_golden = [
    # enquiry — asking about products / info
    ("do you have shimmer oil", "enquiry"),
    ("what's the price of the bundle", "enquiry"),
    ("tell me about Amfa Cosmetics", "enquiry"),

    # cart_add
    ("add the shimmer oil to my cart", "cart_add"),

    # cart_remove
    ("remove the body oil", "cart_remove"),

    # cart_view
    ("whats in my cart", "cart_view"),

    # payments (your "checkout" intent)
    ("I want to checkout", "payments"),
    ("how do I pay", "payments"),

    # chitchat
    ("hi", "chitchat"),
    ("thanks", "chitchat"),
]