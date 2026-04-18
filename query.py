with open('novelove_core.py', encoding='utf-8') as f: lines = f.readlines()
for i, line in enumerate(lines):
  if 'send_inventory_report' in line:
    print('Found at', i)
