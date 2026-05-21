import sys
print("Starting test...")
try:
    line = input("test> ").strip()
    print(f"Read: '{line}'")
    if line:
        print(f"Processing: {line}")
    else:
        print("Empty line")
except Exception as e:
    print(f"Error: {e}")

print("Done")