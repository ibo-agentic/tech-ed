"""
Math verification layer for accounting answers.
Parses Bangla numeral tables from LLM responses and checks arithmetic.
"""
import re


# Bangla → English digit mapping
BANGLA_TO_ENG = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


def to_int(bangla_or_eng: str) -> int:
    """Convert '৪,২০,০০০' or '4,20,000' → 420000."""
    if not bangla_or_eng:
        return 0
    s = str(bangla_or_eng).translate(BANGLA_TO_ENG)
    s = re.sub(r'[^\d]', '', s)  # strip commas, spaces, periods
    return int(s) if s else 0


def extract_totals(text: str) -> dict:
    """
    Find line items that look like totals/subtotals in the response.
    Returns dict of {label: amount} for verification.
    """
    totals = {}
    
    # Patterns that indicate a total line
    patterns = [
        # মোট প্রাপ্তি ৯,২২,০০০
        (r'মোট প্রাপ্তি[^\d০-৯]*([০-৯,\d]+)', 'total_receipts'),
        (r'মোট প্রদান[^\d০-৯]*([০-৯,\d]+)', 'total_payments'),
        (r'মোট আয়[^\d০-৯]*([০-৯,\d]+)', 'total_income'),
        (r'মোট ব্যয়[^\d০-৯]*([০-৯,\d]+)', 'total_expense'),
        (r'মোট সম্পদ[^\d০-৯]*([০-৯,\d]+)', 'total_assets'),
        (r'মোট দায়[^\d০-৯]*([০-৯,\d]+)', 'total_liab_capital'),
        (r'(?:সমাপনী নগদ|হাতে নগদ \(.+?\)|নগদ তহবিল \(সমাপনী\))[^\d০-৯]*([০-৯,\d]+)', 'closing_cash'),
        (r'(?:আয় উদ্বৃত্ত|আয়-উদ্বৃত্ত|আয়তিরিক্ত|আয়ের উদ্বৃত্ত)[^\d০-৯]*([০-৯,\d]+)', 'surplus'),
    ]
    
    for pattern, key in patterns:
        matches = re.findall(pattern, text)
        if matches:
            # Take the LAST match (usually the actual total, not intermediate)
            totals[key] = to_int(matches[-1])
    
    return totals


def verify_accounting_answer(text: str) -> tuple[bool, list[str]]:
    """
    Check if the accounting answer's math balances.
    Returns (is_valid, list_of_errors).
    """
    totals = extract_totals(text)
    errors = []
    
    # Check 1: Receipts == Payments (Section ক)
    if 'total_receipts' in totals and 'total_payments' in totals:
        if totals['total_receipts'] != totals['total_payments']:
            errors.append(
                f"প্রাপ্তি ({totals['total_receipts']}) ≠ প্রদান ({totals['total_payments']})"
            )
    
    # Check 2: Assets == Liabilities + Capital (Section গ — balance sheet)
    if 'total_assets' in totals and 'total_liab_capital' in totals:
        if totals['total_assets'] != totals['total_liab_capital']:
            errors.append(
                f"মোট সম্পদ ({totals['total_assets']}) ≠ মোট দায় ও মূলধন ({totals['total_liab_capital']})"
            )
    
    # Check 3: Surplus exists and is positive (most family accounting problems have surplus)
    if 'surplus' in totals and totals['surplus'] < 0:
        errors.append(f"Surplus is negative ({totals['surplus']}) — likely deficit miscalculation")
    
    is_valid = len(errors) == 0 and len(totals) >= 2  # need at least 2 numbers to verify
    return is_valid, errors


if __name__ == "__main__":
    # Quick self-test
    test_correct = """
    মোট প্রাপ্তি ৯,২২,০০০
    মোট প্রদান ৯,২২,০০০
    মোট সম্পদ ৫,৪৭,৫০০
    মোট দায় ও মূলধন ৫,৪৭,৫০০
    """
    
    test_wrong = """
    মোট প্রাপ্তি ৯,২২,০০০
    মোট প্রদান ৯,২২,০০০
    মোট সম্পদ ৬,২৭,৫০০
    মোট দায় ও মূলধন ১,৭০,৫০০
    """
    
    print("Test 1 (correct):", verify_accounting_answer(test_correct))
    print("Test 2 (wrong):", verify_accounting_answer(test_wrong))