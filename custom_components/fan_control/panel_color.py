"""Optional native RGB picker value; absent colors preserve old panel contracts."""
def add_color(result, value):
    if "color" in value:
        color = value["color"]
        if not isinstance(color, list) or len(color) != 3 or any(type(component) is not int or not 0 <= component <= 255 for component in color):
            raise ValueError("Select a valid RGB color")
        result["color"] = list(color)
    return result
