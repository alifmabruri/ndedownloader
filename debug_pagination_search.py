from pathlib import Path
import re

path = Path('debug_pagination.html')
text = path.read_text(encoding='utf-8', errors='ignore')
patterns = [
    'pagination', 'next', 'berikutnya', 'selanjutnya', 'chevron-right',
    'aria-label', 'page-item', 'page-link', 'rel="next"', 'class*=pagination',
    'class="pagination"', 'data-state', 'aria-expanded', 'aria-controls',
    'lucide', 'svg'
]
for term in patterns:
    print('===', term)
    m = re.search(re.escape(term), text, re.IGNORECASE)
    if not m:
        print('NOT FOUND')
    else:
        start = max(0, m.start() - 220)
        end = min(len(text), m.end() + 220)
        print(text[start:end].replace('\n', ' '))
    print()