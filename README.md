# خطوات التشغيل
1. تيليجرام: كلّم @BotFather واعمل /newbot وخد الـ TOKEN. ابعت أي رسالة للبوت، وافتح
   https://api.telegram.org/bot<TOKEN>/getUpdates وخد رقم chat id.
2. اعمل repo جديد (Public، عشان التشغيل يبقى مجاني بلا حدود) على GitHub وارفع الملفات دي.
3. Settings > Secrets and variables > Actions: ضيف TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID.
4. تبويب Actions > job-bot > Run workflow (أول مرة بتحفظ الوظايف الحالية بس).
5. بعدها بيشتغل لوحده كل 5 دقايق.

# ملاحظات
- شغلني بيعرض الوظايف بالجافاسكريبت، فالبوت بيستخدم متصفح مخفي (Playwright) معاه.
- عرب جوبز مش متضاف: موقعهم بيمنع الدخول الآلي في ملف robots.txt.
