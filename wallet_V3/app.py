import sqlite3
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date

app = Flask(__name__)
app.config['SECRET_KEY'] = 'my_secure_finance_key_2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance_v4.db'
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    debts = db.relationship('Debt', backref='owner', lazy=True)
    cards = db.relationship('Card', backref='owner', lazy=True)

class Card(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Debt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    card_name = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    original_amount = db.Column(db.Float, nullable=False, default=0.0)
    amount = db.Column(db.Float, nullable=False)
    debt_type = db.Column(db.String(20))
    months = db.Column(db.Integer, default=1)
    paid_this_month = db.Column(db.Float, default=0.0)
    date_added = db.Column(db.Date, nullable=True, default=date.today)
    payment_note = db.Column(db.Text, nullable=True, default="")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@app.context_processor
def inject_functions():
    return dict(max=max, min=min, int=int, round=round)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
@login_required
def index():
    user_cards = Card.query.filter_by(user_id=current_user.id).all()
    card_list = [c.name for c in user_cards]
    selected_card = request.args.get('card', 'all' if card_list else None)
    
    today = date.today()
    first_day_this_month = date(today.year, today.month, 1)
    all_user_debts = Debt.query.filter_by(user_id=current_user.id).all()
    
    # حساب الإحصائيات العامة (للمربعات العلوية)
    grand_monthly_total = 0
    grand_full_total = sum([d.amount for d in all_user_debts])
    
    for d in all_user_debts:
        if d.date_added < first_day_this_month:
            target = (d.original_amount / d.months) if d.debt_type == 'قسط' else d.amount
            grand_monthly_total += max(target, 0)

    # --- الجزء المهم: حساب مبالغ كل بنك للنظرة العامة ---
    bank_stats = {}
    for card_name in card_list:
        # تصفية مديونيات هذا البنك فقط
        b_debts = [d for d in all_user_debts if d.card_name == card_name]
        
        # إجمالي المديونية للبنك
        total = sum([d.amount for d in b_debts])
        
        # المطلوب الآن للبنك (العمليات القديمة فقط)
        monthly = sum([
            ((d.original_amount / d.months) if d.debt_type == 'قسط' else d.amount)
            for d in b_debts if d.date_added < first_day_this_month
        ])
        
        bank_stats[card_name] = {'total': total, 'monthly': monthly}

    # تحديد المديونيات المعروضة في الجدول
    if selected_card == 'all':
        debts = all_user_debts
    else:
        debts = [d for d in all_user_debts if d.card_name == selected_card]

    return render_template('index.html', 
                           debts=debts, card_list=card_list, 
                           selected_card=selected_card, 
                           grand_monthly_total=grand_monthly_total,
                           grand_full_total=grand_full_total,
                           bank_stats=bank_stats, # هذا المتغير هو الذي يظهر الأرقام
                           name=current_user.username, 
                           today_date=today.strftime('%Y-%m-%d'),
                           first_day_this_month=first_day_this_month)

@app.route('/add', methods=['POST'])
@login_required
def add():
    card_sel = request.form.get('card_select')
    card_name = request.form.get('card_name_manual').strip() if card_sel == 'other' else card_sel
    date_str = request.form.get('date_added')
    amount_val = float(request.form['amount'])

    new_debt = Debt(
        card_name=card_name,
        title=request.form['title'],
        amount=amount_val,
        original_amount=amount_val,
        debt_type=request.form['debt_type'],
        months=int(request.form.get('months', 1)) if request.form['debt_type'] == 'قسط' else 1,
        date_added=datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else date.today(),
        user_id=current_user.id
    )
    db.session.add(new_debt)
    db.session.commit()
    return redirect(url_for('index', card=card_name))

@app.route('/pay/<int:id>', methods=['POST'])
@login_required
def pay_debt(id):
    debt = Debt.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    paid_amount = float(request.form.get('paid_amount', 0))
    payment_comment = request.form.get('payment_comment', '').strip()
    
    # الترحيل التلقائي للخصم: الخصم مباشرة من المديونية الكلية
    debt.amount -= paid_amount
    
    # تحديث عدد الشهور تلقائياً إذا كان قسطاً وتم سداد القسط الشهري
    if debt.debt_type == 'قسط':
        monthly_inst = debt.original_amount / (debt.months if debt.months > 0 else 1)
        if paid_amount >= (monthly_inst - 1): # سماحية بسيطة للكسور
            if debt.months > 1:
                debt.months -= 1
            else:
                db.session.delete(debt)
                db.session.commit()
                return redirect(url_for('index', card=debt.card_name))

    # إضافة الملاحظة في السجل
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_entry = f"✅ {date_now}: سداد {paid_amount:,.2f} ج.م"
    if payment_comment:
        log_entry += f" (ملاحظة: {payment_comment})"
    
    debt.payment_note = (log_entry + "\n" + (debt.payment_note or "")).strip()
    
    if debt.amount <= 0.1:
        db.session.delete(debt)
    
    db.session.commit()
    return redirect(url_for('index', card=debt.card_name))

# تم حذف مسار confirm_month (الاعتماد) لأنه أصبح أوتوماتيكياً

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    debt = Debt.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        debt.title = request.form['title']
        debt.amount = float(request.form['amount'])
        debt.original_amount = float(request.form['original_amount'])
        debt.debt_type = request.form['debt_type']
        debt.months = int(request.form.get('months', 1))
        db.session.commit()
        return redirect(url_for('index', card=debt.card_name))
    return render_template('edit.html', debt=debt)


@app.route('/edit_card/<old_name>', methods=['POST'])
@login_required
def edit_card(old_name):
    new_name = request.form.get('new_name').strip()
    if new_name:
        card = Card.query.filter_by(name=old_name, user_id=current_user.id).first()
        if card:
            card.name = new_name
            Debt.query.filter_by(card_name=old_name, user_id=current_user.id).update({Debt.card_name: new_name})
            db.session.commit()
    return redirect(url_for('index', card=new_name if new_name else None))

@app.route('/delete/<int:id>')
@login_required
def delete(id):
    debt = Debt.query.get_or_404(id)
    card = debt.card_name
    db.session.delete(debt)
    db.session.commit()
    return redirect(url_for('index', card=card))

@app.route('/add_card', methods=['POST'])
@login_required
def add_card():
    card_name = request.form.get('new_card_name').strip()
    if card_name and not Card.query.filter_by(name=card_name, user_id=current_user.id).first():
        db.session.add(Card(name=card_name, user_id=current_user.id))
        db.session.commit()
    return redirect(url_for('index', card=card_name))

@app.route('/delete_card/<string:card_name>')
@login_required
def delete_card(card_name):
    Debt.query.filter_by(card_name=card_name, user_id=current_user.id).delete()
    Card.query.filter_by(name=card_name, user_id=current_user.id).delete()
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        pw = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        db.session.add(User(username=request.form['username'], password=pw))
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)