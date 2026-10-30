"""The sign-up page (ADR 0031): one phone-sized form, written out by the server in Finnish, English
or Chinese, with no script and nothing loaded from anywhere, so it works on any phone's browser
and a Content-Security-Policy of `default-src 'none'` holds. The Wilma password goes only into its
own password field, posted with the form, and never comes back in a page.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from .invites import LANGUAGES, Recipient

NAMES = {"fi": "Suomi", "en": "English", "zh": "中文"}
TEXT = {
    "en": {
        "title": "Sign up for Parent Recap",
        "intro": "Parent Recap reads your children's Wilma every evening and emails you a short "
                 "Brief of what matters. Sign in to Wilma once here, and we do the rest.",
        "wilma": "Wilma",
        "town": "Town",
        "town_hint": "Type your town in Finnish, such as Espoo, and pick it from the list.",
        "username": "Wilma username",
        "password": "Wilma password",
        "password_hint": "The same as on the Wilma website or app. It is kept only on our "
                         "server, to read Wilma for you.",
        "recipients": "Who gets the Brief",
        "recipients_hint": "Check each address and the language it reads the Brief in. You may "
                           "add one more.",
        "address": "Email address",
        "language": "Language",
        "another": "Another address (optional)",
        "submit": "Continue",
        "myclub": "MyClub (optional)",
        "myclub_hint": "If a child's club uses MyClub, paste the child's calendar link from MyClub "
                       "(it starts with webcal:// or https://), and their practices and games come "
                       "in the Brief too. Leave it empty to skip.",
        "sign_up": "Sign up",
        "wait": "Signing up takes a minute or two. Keep this page open until it says you're "
                "signed up.",
        "wrong-password": "Wilma didn't accept that username and password. Check them and try "
                          "again.",
        "no-kids": "This Wilma account has no children in it. Sign in with a guardian's account.",
        "sign-in-failed": "Wilma couldn't be reached just now, so nothing was kept. Try again in "
                          "a while.",
        "invalid-town": "Pick your town from the list.",
        "invalid-sign-in": "Fill in the Wilma username and password.",
        "invalid-recipients": "Give at least one email address, written as name@example.com.",
        "not-a-myclub-link": "{kid}: that isn't a MyClub calendar link. Copy the calendar's "
                             "subscription link in MyClub, or leave it empty.",
        "link-failed": "{kid}: the MyClub link didn't open. Check it, or leave it empty to skip "
                       "MyClub.",
        "not-a-calendar": "{kid}: the link opened, but there's no calendar at it. Copy the "
                          "calendar's subscription link in MyClub, or leave it empty.",
        "same-link": "{kid}: this is the same link as another child's. Each child has their own "
                     "MyClub calendar link.",
        "paste_again": "The links aren't shown again, since each opens a child's calendar: paste "
                       "the ones you want once more.",
        "expired": "That took too long, so nothing was kept. Sign in to Wilma again.",
        "failed": "Something went wrong on our side, and nothing was kept. Try again later, or "
                  "tell whoever sent you this link.",
        "done": "You're signed up",
        "sent": "Your first Brief has just been sent by email. From now on a Brief comes each "
                "evening at {time} when there's something new.",
        "junk": "Hotmail and Outlook.com may put the first Brief in Junk. If it's there, mark it "
                "\"Not junk\" once.",
    },
    "fi": {
        "title": "Ota Parent Recap käyttöön",
        "intro": "Parent Recap lukee lastesi Wilman joka ilta ja lähettää sinulle sähköpostilla "
                 "lyhyen koosteen siitä, mikä on tärkeää. Kirjaudu Wilmaan tässä kerran, niin me "
                 "hoidamme loput.",
        "wilma": "Wilma",
        "town": "Kunta",
        "town_hint": "Kirjoita kuntasi nimi suomeksi, esimerkiksi Espoo, ja valitse se listasta.",
        "username": "Wilman käyttäjätunnus",
        "password": "Wilman salasana",
        "password_hint": "Sama kuin Wilman verkkosivulla tai sovelluksessa. Se säilytetään vain "
                         "palvelimellamme, jotta voimme lukea Wilmaa puolestasi.",
        "recipients": "Kuka saa koosteen",
        "recipients_hint": "Tarkista osoitteet ja kieli, jolla kukin lukee koosteen. Voit lisätä "
                           "yhden osoitteen.",
        "address": "Sähköpostiosoite",
        "language": "Kieli",
        "another": "Toinen osoite (vapaaehtoinen)",
        "submit": "Jatka",
        "myclub": "MyClub (vapaaehtoinen)",
        "myclub_hint": "Jos lapsen seura käyttää MyClubia, liitä lapsen kalenterin linkki "
                       "MyClubista (se alkaa webcal:// tai https://), niin harjoitukset ja pelit "
                       "tulevat myös koosteeseen. Jätä kenttä tyhjäksi, jos et tarvitse sitä.",
        "sign_up": "Ota käyttöön",
        "wait": "Käyttöönotto kestää minuutin tai kaksi. Pidä tämä sivu auki, kunnes se kertoo, "
                "että olet mukana.",
        "wrong-password": "Wilma ei hyväksynyt käyttäjätunnusta ja salasanaa. Tarkista ne ja "
                          "yritä uudelleen.",
        "no-kids": "Tällä Wilma-tunnuksella ei ole lapsia. Kirjaudu huoltajan tunnuksella.",
        "sign-in-failed": "Wilmaan ei juuri nyt saatu yhteyttä, joten mitään ei tallennettu. "
                          "Yritä hetken päästä uudelleen.",
        "invalid-town": "Valitse kuntasi listasta.",
        "invalid-sign-in": "Täytä Wilman käyttäjätunnus ja salasana.",
        "invalid-recipients": "Anna vähintään yksi sähköpostiosoite muodossa nimi@esimerkki.fi.",
        "not-a-myclub-link": "{kid}: tämä ei ole MyClub-kalenterin linkki. Kopioi kalenterin "
                             "tilauslinkki MyClubista tai jätä kenttä tyhjäksi.",
        "link-failed": "{kid}: MyClub-linkki ei auennut. Tarkista se tai jätä kenttä tyhjäksi, "
                       "niin MyClub jää pois.",
        "not-a-calendar": "{kid}: linkki aukesi, mutta siinä ei ole kalenteria. Kopioi kalenterin "
                          "tilauslinkki MyClubista tai jätä kenttä tyhjäksi.",
        "same-link": "{kid}: tämä on sama linkki kuin toisella lapsella. Jokaisella lapsella on "
                     "oma MyClub-kalenterin linkkinsä.",
        "paste_again": "Linkkejä ei näytetä uudelleen, koska ne avaavat lapsen kalenterin: liitä "
                       "haluamasi linkit uudelleen.",
        "expired": "Tämä kesti liian kauan, joten mitään ei tallennettu. Kirjaudu Wilmaan "
                   "uudelleen.",
        "failed": "Meidän päässämme meni jotain vikaan, eikä mitään tallennettu. Yritä myöhemmin "
                  "uudelleen tai kerro asiasta linkin lähettäjälle.",
        "done": "Olet mukana",
        "sent": "Ensimmäinen kooste lähetettiin juuri sähköpostiisi. Tästä eteenpäin kooste tulee "
                "joka ilta kello {time}, kun on jotain uutta.",
        "junk": "Hotmail ja Outlook.com voivat laittaa ensimmäisen koosteen roskapostiin. Jos se "
                "on siellä, merkitse se kerran \"Ei roskapostia\".",
    },
    "zh": {
        "title": "开始使用 Parent Recap",
        "intro": "Parent Recap 每天晚上读取孩子的 Wilma，把重要的内容整理成一份简短的摘要发到你的邮箱。"
                 "在这里登录一次 Wilma，其余的交给我们。",
        "wilma": "Wilma",
        "town": "城市",
        "town_hint": "用芬兰语输入你所在的城市，例如 Espoo，然后在列表里选中它。",
        "username": "Wilma 用户名",
        "password": "Wilma 密码",
        "password_hint": "和 Wilma 网站或 App 上的相同。它只保存在我们的服务器上，用来替你读取 Wilma。",
        "recipients": "谁收到摘要",
        "recipients_hint": "确认每个邮箱地址，以及它阅读摘要用的语言。你可以再添加一个。",
        "address": "邮箱地址",
        "language": "语言",
        "another": "另一个地址（可选）",
        "submit": "继续",
        "myclub": "MyClub（可选）",
        "myclub_hint": "如果孩子的俱乐部用 MyClub，把孩子在 MyClub 里的日历链接粘贴到这里（以 webcal:// "
                       "或 https:// 开头），训练和比赛也会出现在摘要里。不需要就留空。",
        "sign_up": "开始使用",
        "wait": "开通需要一两分钟。请保持这个页面打开，直到它显示开通成功。",
        "wrong-password": "Wilma 没有接受这个用户名和密码。请检查后再试一次。",
        "no-kids": "这个 Wilma 账号下没有孩子。请用家长（监护人）的账号登录。",
        "sign-in-failed": "现在连不上 Wilma，所以什么都没有保存。请过一会儿再试。",
        "invalid-town": "请从列表里选择你的城市。",
        "invalid-sign-in": "请填写 Wilma 用户名和密码。",
        "invalid-recipients": "请至少填写一个邮箱地址，格式如 name@example.com。",
        "not-a-myclub-link": "{kid}：这不是 MyClub 日历链接。请在 MyClub 里复制日历的订阅链接，或者留空。",
        "link-failed": "{kid}：这个 MyClub 链接打不开。请检查一下，或者留空跳过 MyClub。",
        "not-a-calendar": "{kid}：链接能打开，但里面没有日历。请在 MyClub 里复制日历的订阅链接，或者留空。",
        "same-link": "{kid}：这个链接和另一个孩子的一样。每个孩子都有自己的 MyClub 日历链接。",
        "paste_again": "链接不会再显示，因为它们能打开孩子的日历：请把需要的链接再粘贴一次。",
        "expired": "等待时间太长，什么都没有保存。请重新登录 Wilma。",
        "failed": "我们这边出了问题，什么都没有保存。请稍后再试，或告诉发给你这个链接的人。",
        "done": "开通成功",
        "sent": "第一份摘要刚刚已经发到你的邮箱。之后每天晚上 {time}，有新消息时就会发一份摘要。",
        "junk": "Hotmail 和 Outlook.com 可能会把第一份摘要放进垃圾邮件。如果在那里找到它，请把它标记为“非垃圾邮件”一次。",
    },
}
STYLE = """
body{font:16px/1.45 system-ui,-apple-system,sans-serif;margin:0;background:#f6f5f2;color:#1d1d1b}
main{max-width:34rem;margin:0 auto;padding:1rem 1rem 3rem}
h1{font-size:1.4rem;margin:.5rem 0 1rem}h2{font-size:1.1rem;margin:1.5rem 0 .5rem}
nav{display:flex;gap:1rem;justify-content:flex-end}nav a{color:#1d1d1b}nav b{font-weight:600}
label{display:block;margin:.75rem 0 .25rem;font-weight:600}
input,select{box-sizing:border-box;width:100%;font:inherit;padding:.6rem;border:1px solid #b9b6ad;
border-radius:.4rem;background:#fff}
.row{display:flex;gap:.5rem}.row input{flex:3}.row select{flex:1;min-width:6rem}
.hint{color:#5b5a55;font-size:.9rem;margin:.25rem 0}
.error{background:#fde8e4;border:1px solid #e0a197;border-radius:.4rem;padding:.75rem}
button{margin-top:1.5rem;width:100%;font:inherit;font-weight:600;padding:.8rem;border:0;
border-radius:.4rem;background:#2d5c4f;color:#fff}
"""
MAX_RECIPIENTS = 10  # the Brief relay's, for one message


@dataclass
class Typed:
    """What the family typed, to show again when the page comes back with why: never the
    password."""
    town: str = ""
    username: str = ""
    recipients: list[Recipient] = field(default_factory=list)


def form(language: str, typed: Typed, towns: list[str], error: str | None = None) -> str:
    t = TEXT[language]
    rows = [*typed.recipients[:MAX_RECIPIENTS - 1], Recipient("", language)]
    recipients = "".join(_recipient(i, r, t, last=i == len(rows) - 1) for i, r in enumerate(rows))
    options = "".join(f"<option value=\"{escape(town)}\">" for town in towns)
    return _page(language, t["title"], f"""
<p>{escape(t['intro'])}</p>
{f'<p class="error" role="alert">{escape(t[error])}</p>' if error else ''}
<form method="post" autocomplete="on">
<input type="hidden" name="lang" value="{language}">
<h2>{escape(t['wilma'])}</h2>
<label for="town">{escape(t['town'])}</label>
<input id="town" name="town" list="towns" required autocomplete="off" value="{escape(typed.town)}">
<datalist id="towns">{options}</datalist>
<p class="hint">{escape(t['town_hint'])}</p>
<label for="username">{escape(t['username'])}</label>
<input id="username" name="username" required autocomplete="username" autocapitalize="none"
 spellcheck="false" value="{escape(typed.username)}">
<label for="password">{escape(t['password'])}</label>
<input id="password" name="password" type="password" required autocomplete="current-password">
<p class="hint">{escape(t['password_hint'])}</p>
<h2>{escape(t['recipients'])}</h2>
<p class="hint">{escape(t['recipients_hint'])}</p>
{recipients}
<button type="submit">{escape(t['submit'])}</button>
</form>""")


def myclub(language: str, kids: list[str], error: str | None = None,
           kid: str | None = None) -> str:
    """The second step, once Wilma has said who the Kids are: a MyClub calendar link for each,
    which may be left empty. A link is never shown back, as the password isn't (ADR 0007)."""
    t = TEXT[language]
    fields = "".join(f"""<label for="m{i}">{escape(name)}</label>
<input id="m{i}" name="m{i}" inputmode="url" autocomplete="off" autocapitalize="none"
 spellcheck="false">
""" for i, name in enumerate(kids))
    alert = (f'<p class="error" role="alert">{escape(t[error].format(kid=kid))}</p>'
             f'<p class="hint">{escape(t["paste_again"])}</p>') if error else ""
    return _page(language, t["title"], f"""
{alert}
<form method="post" autocomplete="off">
<input type="hidden" name="lang" value="{language}">
<input type="hidden" name="step" value="myclub">
<h2>{escape(t['myclub'])}</h2>
<p class="hint">{escape(t['myclub_hint'])}</p>
{fields}
<button type="submit">{escape(t['sign_up'])}</button>
<p class="hint">{escape(t['wait'])}</p>
</form>""", languages=False)


def _recipient(i: int, r: Recipient, t: dict[str, str], last: bool) -> str:
    label = t["another"] if last else t["address"]
    choices = "".join(f"<option value=\"{code}\"{' selected' if code == r.language else ''}>"
                      f"{NAMES[code]}</option>" for code in LANGUAGES)
    return f"""<label for="r{i}">{escape(label)}</label>
<div class="row"><input id="r{i}" name="r{i}" type="email" autocomplete="email"
 value="{escape(r.address)}"{' required' if i == 0 else ''}>
<select name="r{i}_language" aria-label="{escape(t['language'])}">{choices}</select></div>
"""


def done(language: str, time: str, junk: bool) -> str:
    """Signed up: the first Brief sent, and when the next ones come. `junk` when a Recipient is
    on Hotmail or Outlook.com, which may file a new sender's first mail as Junk."""
    t = TEXT[language]
    hint = f'\n<p class="hint">{escape(t["junk"])}</p>' if junk else ""
    return _page(language, t["done"], f"""
<p>{escape(t['sent'].format(time=time))}</p>{hint}""", languages=False)


def failed(language: str) -> str:
    t = TEXT[language]
    return _page(language, t["title"], f'<p class="error" role="alert">{escape(t["failed"])}</p>',
                 languages=False)


def _page(language: str, title: str, body: str, languages: bool = True) -> str:
    nav = "".join(f"<b>{NAMES[code]}</b>" if code == language
                  else f"<a href=\"?lang={code}\" hreflang=\"{code}\">{NAMES[code]}</a>"
                  for code in LANGUAGES) if languages else ""
    return f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(title)}</title>
<style>{STYLE}</style>
</head>
<body><main>
<nav>{nav}</nav>
<h1>{escape(title)}</h1>
{body}
</main></body>
</html>
"""
