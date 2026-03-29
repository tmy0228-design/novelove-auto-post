import re

html_sample = '''
<div class="speech-bubble-left"><img src="/wp-content/uploads/icons/蓮.png" alt="蓮" />
<div class="speech-text">
やあ！今週のランキングだね！
すっごく楽しみ！
</div>
</div>
'''

def _clean_bubble(m):
    text = m.group(0)
    # Remove newlines between tags (e.g., >\n<)
    text = re.sub(r'>\s*\n\s*<', '><', text)
    # Convert remaining inner newlines to <br />
    text = text.replace('\n', '<br />')
    return text

fixed = re.sub(r'<div class="speech-bubble-(?:left|right)".*?</div>\s*</div>', _clean_bubble, html_sample, flags=re.DOTALL)
print("FIXED:")
print(fixed)
