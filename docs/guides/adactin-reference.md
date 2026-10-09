# Adactin Hotel Reservation App — QA Reference Guide

> Purpose: This document is a ground-truth reference for an AI coding agent generating Playwright (TypeScript) end-to-end tests against the Adactin Hotel Reservation demo application. It describes the exact page flow, DOM selectors, valid test data, and assertion patterns. Treat every selector and value below as authoritative — do not invent alternative IDs, dropdown labels, or URLs.

---

## 1. Application Overview & Core Settings

- **Base URL:** `https://adactinhotelapp.com/`
- **App type:** Public QA training/demo hotel booking site (widely used for Selenium/Playwright/Cypress practice). Session-based, server-rendered pages with `.php` extensions.
- **Authentication Credentials (Build 1):**
  - Username: `ADACTINFORQA`
  - Password: `3U0515`
- **Important — Build 1 vs Build 2:** The site explicitly states Build 1 "has been developed with known defects" and functional/automation scripts **are expected to fail** on it. Build 2 has the defects fixed and is the build automation scripts should be validated against. If a generated test is asserting expected-success flows, prefer targeting Build 2 (linked from the homepage as "Go to Build 2") unless the task is specifically to reproduce a known Build 1 defect.
- **Session behavior:** Pages after login are session-gated. Navigating directly to a post-login URL (e.g., `SearchHotel.php`) without an active session will redirect back to the login page. Tests must always start each scenario with the login step.
- **No native `<button>` elements:** Every clickable action element on this site (Login, Search Submit, Continue, Book Now) is an `<input type="submit">` or `<input type="button">`, **not** a `<button>` tag. Playwright locators/assertions should target `input[...]`, and `getByRole('button', ...)` will still match these because `<input type="submit">` has an implicit ARIA role of `button` — but a raw tag-name selector must use `input`, not `button`.

---

## 2. Detailed Page-by-Page Sequence & Selectors

### Page A: Login Page (`index.php`)

**Visual description:** Two-column layout. Left side is marketing content for the Adactin mobile app. Right side ("Existing User Login - Build 1") has a Username field, Password field, a "Login" button, "Forgot Password?" link, and "New User Register Here" link.

**Selectors:**
| Element | Selector | Notes |
|---|---|---|
| Username input | `input#username` | `.fill('ADACTINFORQA')` |
| Password input | `input#password` | `.fill('3U0515')` |
| Login button | `input#login` | Also matchable via `input[name="login"]`. It is an `<input type="submit">`, click with `.click()`. |

**Action sequence:**
```ts
await page.goto('https://adactinhotelapp.com/');
await page.locator('input#username').fill('ADACTINFORQA');
await page.locator('input#password').fill('3U0515');
await page.locator('input#login').click();
```

**Assertion:** After successful login, the browser redirects to `SearchHotel.php`.
```ts
await expect(page).toHaveURL(/SearchHotel\.php/);
```

---

### Page B: Search Hotel Page (`SearchHotel.php`)

**Visual description:** A top nav bar with "Search Hotel | Booked Itinerary | Change Password | Logout" links and a welcome message ("Hello ADACTINFORQA!"). Below it, a search form with Location, Hotels, Room Type, Number of Rooms, Check-in/Check-out date pickers, Adults per Room, and Children per Room, followed by "Search" and "Reset" buttons.

**Selectors:**
| Element | Selector | Type | Example valid values |
|---|---|---|---|
| Welcome message | `input#username_show` | read-only input | Value is `Hello ADACTINFORQA!` |
| Location | `select#location` | `.selectOption()` | `'Sydney'`, `'Melbourne'` |
| Hotels | `select#hotels` | `.selectOption()` | `'Hotel Creek'`, `'Hotel Sunshine'` |
| Room Type | `select#room_type` | `.selectOption()` | `'Standard'`, `'Deluxe'` |
| Number of Rooms | `select#room_nos` | `.selectOption()` | `'1 - One'`, `'2 - Two'` |
| Check-in Date | `input#datepick_in` | `.fill()` | Format `DD/MM/YYYY` |
| Check-out Date | `input#datepick_out` | `.fill()` | Format `DD/MM/YYYY` |
| Adults per Room | `select#adult_room` | `.selectOption()` | `'1'`, `'2'`, `'3'` — **ID is `adult_room`, singular, not `adults_room`** |
| Children per Room | `select#child_room` | `.selectOption()` | `'0'`, `'1'`, `'2'` — **ID is `child_room`, singular, not `children_room`** |
| Submit button | `input#Submit` | `.click()` | **Capital `S`** — `input#Submit`, not `input#submit` |

**Action sequence:**
```ts
await expect(page.locator('input#username_show')).toHaveValue('Hello ADACTINFORQA!');
await page.locator('select#location').selectOption('Sydney');
await page.locator('select#hotels').selectOption('Hotel Creek');
await page.locator('select#room_type').selectOption('Standard');
await page.locator('select#room_nos').selectOption('1 - One');
await page.locator('input#datepick_in').fill('20/08/2026');
await page.locator('input#datepick_out').fill('25/08/2026');
await page.locator('select#adult_room').selectOption('1');
await page.locator('select#child_room').selectOption('0');
await page.locator('input#Submit').click();
```

**Notes on date pickers:** `#datepick_in` and `#datepick_out` are backed by a JS date-picker widget, but they also accept direct text input via `.fill()`. If `.fill()` does not trigger the widget's internal validation in a given test, fall back to clicking the field and using the calendar UI, but `.fill()` with `DD/MM/YYYY` is the standard, reliable approach used across the QA community for this app. Check-out date must be **after** check-in date or the form will reject submission.

**Assertion:** URL redirects to `SelectHotel.php`.
```ts
await expect(page).toHaveURL(/SelectHotel\.php/);
```

---

### Page C: Select Hotel Page (`SelectHotel.php`)

**Visual description:** A results table listing hotels matching the search criteria, each row with a radio button, hotel name, price per night, and total price. Below the table are "Continue" and "Cancel Booking" buttons.

**Selectors:**
| Element | Selector | Notes |
|---|---|---|
| Radio button (first result) | `input#radiobutton_0` | Zero-indexed; subsequent rows are `radiobutton_1`, `radiobutton_2`, etc. |
| Continue button | `input#continue` | `.click()` |

**Action sequence:**
```ts
await page.locator('input#radiobutton_0').check();
await page.locator('input#continue').click();
```

**Assertion:** URL redirects to `BookHotel.php`.
```ts
await expect(page).toHaveURL(/BookHotel\.php/);
```

---

### Page D: Book A Hotel Page (`BookHotel.php`)

**Visual description:** Displays a read-only summary of the selected hotel/dates/price, followed by an editable booking form: First Name, Last Name, Billing Address, Credit Card Type, Credit Card No, Expiry Month/Year, CVV Number, followed by a "Book Now" button.

**Selectors:**
| Element | Selector | Type | Notes |
|---|---|---|---|
| First Name | `input#first_name` | `.fill()` | |
| Last Name | `input#last_name` | `.fill()` | |
| Billing Address | `textarea#address` | `.fill()` | Multi-line textarea, not `input` |
| Credit Card Type | `select#cc_type` | `.selectOption()` | `'VISA'`, `'Master Card'`, `'American Express'` |
| Credit Card No | `input#cc_num` | `.fill()` | Must be exactly 16 digits, numeric only |
| Expiry Month | `select#cc_exp_month` | `.selectOption()` | `'January'` … `'December'` (full month names, not numbers) |
| Expiry Year | `select#cc_exp_year` | `.selectOption()` | `'2026'`, `'2027'`, `'2028'` (4-digit year strings) |
| CVV Number | `input#cc_cvv` | `.fill()` | Must be exactly 3 digits, numeric only |
| Book Now button | `input#book_now` | `.click()` | |

**Action sequence:**
```ts
await page.locator('input#first_name').fill('John');
await page.locator('input#last_name').fill('Doe');
await page.locator('textarea#address').fill('123 Test Street, Sydney, NSW');
await page.locator('select#cc_type').selectOption('VISA');
await page.locator('input#cc_num').fill('1234567890123456');
await page.locator('select#cc_exp_month').selectOption('December');
await page.locator('select#cc_exp_year').selectOption('2028');
await page.locator('input#cc_cvv').fill('123');
await page.locator('input#book_now').click();
```

**Assertion:** URL redirects to `BookingConfirm.php`.
```ts
await expect(page).toHaveURL(/BookingConfirm\.php/);
```

---

### Page E: Booking Confirmation Page (`BookingConfirm.php`)

**Visual description:** Displays a confirmation summary including the generated order number, guest details, and hotel/stay details, plus a "My Itinerary" button/link to view booked orders.

**Selectors:**
| Element | Selector | Notes |
|---|---|---|
| Order Number | `input#order_no` | Read-only input; order number is held in the `value` attribute, not visible text |
| My Itinerary button | `input#my_itinerary` | `.click()` |

**Action sequence:**
```ts
await expect(page).toHaveURL(/BookingConfirm\.php/);
const orderNumber = await page.locator('input#order_no').inputValue();
expect(orderNumber).toMatch(/^\d+$/);
```

---

## 3. Critical QA Rules & Assertions

1. **Read-only display fields must be asserted by value, not text content.**
   `input#username_show` and `input#order_no` are `<input>` elements whose content lives in the `value` attribute, not as rendered text. Playwright's `toContainText()` / `.textContent()` will not see this data.
   - ✅ Correct: `await expect(page.locator('input#username_show')).toHaveValue('Hello ADACTINFORQA!');`
   - ❌ Incorrect: `await expect(page.locator('input#username_show')).toContainText('Hello ADACTINFORQA!');`

2. **Order number extraction pattern:**
   ```ts
   const orderNo = await page.locator('input#order_no').inputValue();
   ```
   Never attempt `.innerText()` or `.textContent()` on this element — both will return an empty string.

3. **All action elements are `<input>` tags, never `<button>`.** When generating tag-based or role-based locators, do not assume `<button>` markup. `page.locator('button#book_now')` will not match; use `page.locator('input#book_now')`.

4. **Date format is strictly `DD/MM/YYYY`.** This applies to both `input#datepick_in` and `input#datepick_out`. Using `MM/DD/YYYY` or ISO `YYYY-MM-DD` will either be silently misinterpreted or rejected by the date picker's validation.

5. **Dropdowns require `.selectOption()` with the visible label text**, not numeric index, for `select#location`, `select#hotels`, `select#room_type`, `select#room_nos`, `select#adult_room`, `select#child_room`, `select#cc_type`, `select#cc_exp_month`, and `select#cc_exp_year`. Passing a raw index or an unlisted string will throw a Playwright "option not found" error.

6. **ID naming is singular, not plural**, for the passenger-count dropdowns: `adult_room` / `child_room` (not `adults_room` / `children_room`).

7. **Submit button ID is capitalized:** `input#Submit` on the Search Hotel page specifically (unlike `input#continue` and `input#book_now`, which are lowercase). CSS ID selectors are case-sensitive — `input#submit` will not match.

8. **Session dependency:** Do not attempt to `page.goto()` directly to `SearchHotel.php`, `SelectHotel.php`, `BookHotel.php`, or `BookingConfirm.php` without first completing the login step in the same browser context/session — the app will redirect back to `index.php`.

9. **Build 1 known-defect caveat:** If tests are run against Build 1 (the default base URL), some functional assertions are expected to fail by design, per the site's own disclosure. For deterministic "happy path should pass" test suites, use the Build 2 URL instead.

---

## 4. Valid Test Data Reference

Use these values verbatim in generated tests instead of placeholders like `"test"` or `"1234"`, which will fail server-side validation.

| Field | Valid Value |
|---|---|
| Username | `ADACTINFORQA` |
| Password | `3U0515` |
| Location | `Sydney` |
| Hotel | `Hotel Creek` |
| Room Type | `Standard` |
| Number of Rooms | `1 - One` |
| Check-in Date | `20/08/2026` (any future date, `DD/MM/YYYY`) |
| Check-out Date | `25/08/2026` (must be after check-in) |
| Adults per Room | `1` |
| Children per Room | `0` |
| First Name | `John` |
| Last Name | `Doe` |
| Billing Address | `123 Test Street, Sydney, NSW` |
| Credit Card Type | `VISA` |
| Credit Card No | `1234567890123456` (exactly 16 digits) |
| Expiry Month | `December` |
| Expiry Year | `2028` |
| CVV | `123` (exactly 3 digits) |

---

## 5. Quick-Reference Selector Table (All Pages)

| Page | Element | Selector |
|---|---|---|
| index.php | Username | `input#username` |
| index.php | Password | `input#password` |
| index.php | Login | `input#login` |
| SearchHotel.php | Welcome text | `input#username_show` |
| SearchHotel.php | Location | `select#location` |
| SearchHotel.php | Hotels | `select#hotels` |
| SearchHotel.php | Room Type | `select#room_type` |
| SearchHotel.php | Number of Rooms | `select#room_nos` |
| SearchHotel.php | Check-in | `input#datepick_in` |
| SearchHotel.php | Check-out | `input#datepick_out` |
| SearchHotel.php | Adults per Room | `select#adult_room` |
| SearchHotel.php | Children per Room | `select#child_room` |
| SearchHotel.php | Submit | `input#Submit` |
| SelectHotel.php | Radio (first result) | `input#radiobutton_0` |
| SelectHotel.php | Continue | `input#continue` |
| BookHotel.php | First Name | `input#first_name` |
| BookHotel.php | Last Name | `input#last_name` |
| BookHotel.php | Address | `textarea#address` |
| BookHotel.php | Credit Card Type | `select#cc_type` |
| BookHotel.php | Credit Card No | `input#cc_num` |
| BookHotel.php | Expiry Month | `select#cc_exp_month` |
| BookHotel.php | Expiry Year | `select#cc_exp_year` |
| BookHotel.php | CVV | `input#cc_cvv` |
| BookHotel.php | Book Now | `input#book_now` |
| BookingConfirm.php | Order Number | `input#order_no` |
| BookingConfirm.php | My Itinerary | `input#my_itinerary` |

---

*End of reference guide.*
