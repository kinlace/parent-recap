"""The sign-up page (ADR 0031): one phone-sized form, written out by the server in Finnish, English
or Chinese, with no script and nothing loaded from anywhere, so it works on any phone's browser
and a Content-Security-Policy of `default-src 'none'` holds. The Wilma, Eepos and eVaka passwords go
only into their own password fields, posted with the form, and never come back in a page.

While the Household is made, the page lists its steps and asks the server again every few seconds
by a `refresh` meta tag, which needs no script and picks up again when the phone wakes.
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
        "name": "What should we call you?",
        "name_hint": "A name for your family, such as the Virtanens or Mia's mum. Only the people who run Parent "
                     "Recap see it, so they know who signed up.",
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
        "no_wilma": "We don't have Wilma",
        "no_wilma_hint": "If your children's school doesn't use Wilma, leave the Wilma fields "
                         "empty and write each child's name here. The Brief then reads their "
                         "clubs' MyClub calendars, their music school's Eepos or their daycare's "
                         "eVaka, which you add next.",
        "kid": "Child's name",
        "myclub_needed": "MyClub",
        "submit": "Continue",
        "myclub": "MyClub (optional)",
        "myclub_hint": "If a child's club uses MyClub, paste the child's calendar link from MyClub "
                       "(it starts with webcal:// or https://), and their practices and games come "
                       "in the Brief too. Don't use MyClub? Leave them empty and press Done.",
        "myclub_needed_hint": "If a child's club uses MyClub, paste the child's calendar link from "
                              "MyClub (it starts with webcal:// or https://), and their practices "
                              "and games come in the Brief too. Leave it empty to skip.",
        "myclub_done": "Done",
        "another_club": "Add another club",
        "another_club_link": "{kid}: another club's link",
        "eepos": "Eepos (optional)",
        "eepos_needed": "Eepos",
        "eepos_hint": "If a child takes lessons at a music or art school that uses Eepos, sign in "
                      "with your Eepos username and password, and their lessons, concerts and "
                      "holidays come in the Brief too. Leave it empty to skip.",
        "eepos_school": "School",
        "eepos_school_hint": "The address in your browser when you sign in to the school's "
                             "Eepos, such as espoo.eepos.fi (not the school's own website). "
                             "Pasting the whole address works too.",
        "eepos_username": "Eepos username",
        "eepos_password": "Eepos password",
        "eepos_password_hint": "It is kept only on our server, to read Eepos for you.",
        "evaka": "eVaka (optional)",
        "evaka_needed": "eVaka",
        "evaka_hint": "If a child goes to a public daycare that uses eVaka, as in Espoo, Tampere, "
                      "Turku and Oulu, sign in with your eVaka email and password, and the "
                      "daycare's trips, closed days, reservation deadlines and messages come in the "
                      "Brief too. Leave it empty to skip.",
        "evaka_city": "City",
        "evaka_city_hint": "Pick your city from the list. For a city not on it, give the address "
                           "of its eVaka, such as evaka.example.fi.",
        "evaka_username": "eVaka email",
        "evaka_password": "eVaka password",
        "evaka_password_hint": "If you have only signed in to eVaka with your bank credentials, "
                               "first set a password under Personal information in eVaka. It is "
                               "kept only on our server, to read eVaka for you.",
        "wait": "Signing up takes a minute or two. Keep this page open until it says you're "
                "signed up.",
        "signing_up": "Signing you up",
        "updates": "This page updates by itself. Keep it open until it says you're signed up; "
                   "it's fine if the phone's screen turns off for a while.",
        "queued": "Another family is signing up just now. Yours starts when theirs is done, so "
                  "none of its steps has started yet.",
        "step_setup": "Setting up Parent Recap for you",
        "step_brief": "Writing and sending your first Brief",
        "wrong-password": "Wilma didn't accept that username and password. Check them and try "
                          "again.",
        "no-kids": "This Wilma account has no children in it. Sign in with a guardian's account.",
        "sign-in-failed": "Wilma couldn't be reached just now, so nothing was kept. Try again in "
                          "a while.",
        "invalid-name": "Write a name we can call you, such as the Virtanens. An email address won't do.",
        "invalid-town": "Pick your town from the list.",
        "invalid-sign-in": "Fill in the Wilma username and password, or your children's names "
                           "if you don't have Wilma.",
        "need-myclub": "Without Wilma, give a MyClub calendar link, an Eepos sign-in or an eVaka "
                       "sign-in, so the Brief has something to read.",
        "invalid-recipients": "Give at least one email address, written as name@example.com.",
        "not-a-myclub-link": "{kid}: that isn't a MyClub calendar link. Copy the calendar's "
                             "subscription link in MyClub, or leave it empty.",
        "link-failed": "{kid}: the MyClub link didn't open. Check it, or leave it empty to skip "
                       "MyClub.",
        "not-a-calendar": "{kid}: the link opened, but there's no calendar at it. Copy the "
                          "calendar's subscription link in MyClub, or leave it empty.",
        "same-link": "{kid}: this is the same link as another child's. Each child has their own "
                     "MyClub calendar link.",
        "eepos-incomplete": "For Eepos, fill in the school, the username and the password, or "
                            "leave all three empty.",
        "eepos-not-a-school": "That isn't an Eepos school. Give the start of its address, such as "
                              "emo for emo.eepos.fi.",
        "eepos-school-website": "That looks like the school's website. Copy the address from your "
                                "browser on the school's Eepos sign-in page instead: it ends in "
                                ".eepos.fi, such as espoo.eepos.fi.",
        "eepos-wrong-password": "Eepos didn't accept that username and password. Check them and "
                                "try again. If you sign in to Eepos by an emailed link, first set "
                                "a password with \"Forgot your password?\" on the school's Eepos "
                                "sign-in page.",
        "eepos-changed": "Eepos answered in a way Parent Recap doesn't understand, so nothing was "
                         "kept. Leave Eepos empty to sign up without it, and tell whoever sent "
                         "you this link.",
        "eepos-no-answer": "Eepos couldn't be reached just now, so nothing was kept. Try again in "
                           "a while, or leave Eepos empty.",
        "evaka-incomplete": "For eVaka, fill in the city, the email and the password, or leave all "
                            "three empty.",
        "evaka-not-a-city": "That isn't a city on eVaka. Pick it from the list, or give the address "
                            "of its eVaka.",
        "evaka-wrong-password": "eVaka didn't accept that email and password. Check them and try "
                                "again. If you have only signed in to eVaka with your bank "
                                "credentials, first set a password under Personal information "
                                "(Omat tiedot) in eVaka.",
        "evaka-changed": "eVaka answered in a way Parent Recap doesn't understand, so nothing was "
                         "kept. Leave eVaka empty to sign up without it, and tell whoever sent you "
                         "this link.",
        "evaka-no-answer": "eVaka couldn't be reached just now, so nothing was kept. Try again in "
                           "a while, or leave eVaka empty.",
        "paste_again": "The links and the Eepos and eVaka passwords aren't shown again, since they "
                       "open a child's calendar: give the ones you want once more.",
        "expired": "That took too long, so nothing was kept. Sign in to Wilma again.",
        "failed": "Something went wrong on our side, and nothing was kept. Try again later, or "
                  "tell whoever sent you this link.",
        "done": "You're signed up",
        "sent": "Your first Brief has just been sent by email. From now on a Brief comes each "
                "evening at {time} when there's something new.",
        "junk": "Hotmail and Outlook.com may put the first Brief in Junk. If it's there, mark it "
                "\"Not junk\" once.",
        "eepos_unmatched": "On Eepos, {names} didn't match any of your children's names, so their "
                           "lessons come in the Brief for the whole family.",
        "evaka_unmatched": "On eVaka, {names} didn't match any of your children's names, so their "
                           "daycare's events come in the Brief for the whole family.",
    },
    "fi": {
        "title": "Ota Parent Recap käyttöön",
        "intro": "Parent Recap lukee lastesi Wilman joka ilta ja lähettää sinulle sähköpostilla "
                 "lyhyen koosteen siitä, mikä on tärkeää. Kirjaudu Wilmaan tässä kerran, niin me "
                 "hoidamme loput.",
        "name": "Millä nimellä voimme kutsua teitä?",
        "name_hint": "Perheenne nimi, esimerkiksi Virtaset tai Mian äiti. Sen näkevät vain Parent Recapin "
                     "ylläpitäjät, jotta he tietävät, kuka liittyi.",
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
        "no_wilma": "Meillä ei ole Wilmaa",
        "no_wilma_hint": "Jos lastesi koulu ei käytä Wilmaa, jätä Wilman kentät tyhjiksi ja "
                         "kirjoita tähän jokaisen lapsen nimi. Kooste lukee silloin seurojen "
                         "MyClub-kalenterit, musiikkiopiston Eepoksen tai päiväkodin eVakan, "
                         "jotka lisäät seuraavaksi.",
        "kid": "Lapsen nimi",
        "myclub_needed": "MyClub",
        "submit": "Jatka",
        "myclub": "MyClub (vapaaehtoinen)",
        "myclub_hint": "Jos lapsen seura käyttää MyClubia, liitä lapsen kalenterin linkki "
                       "MyClubista (se alkaa webcal:// tai https://), niin harjoitukset ja pelit "
                       "tulevat myös koosteeseen. Etkö käytä MyClubia? Jätä kentät tyhjiksi ja "
                       "paina Valmis.",
        "myclub_needed_hint": "Jos lapsen seura käyttää MyClubia, liitä lapsen kalenterin linkki "
                              "MyClubista (se alkaa webcal:// tai https://), niin harjoitukset ja "
                              "pelit tulevat myös koosteeseen. Jätä kenttä tyhjäksi, jos et "
                              "tarvitse sitä.",
        "myclub_done": "Valmis",
        "another_club": "Lisää toinen seura",
        "another_club_link": "{kid}: toisen seuran linkki",
        "eepos": "Eepos (vapaaehtoinen)",
        "eepos_needed": "Eepos",
        "eepos_hint": "Jos lapsi käy musiikki- tai taidekoulua, joka käyttää Eeposta, kirjaudu "
                      "Eepoksen käyttäjätunnuksellasi ja salasanallasi, niin tunnit, konsertit ja "
                      "lomat tulevat myös koosteeseen. Jätä kentät tyhjiksi, jos et tarvitse "
                      "sitä.",
        "eepos_school": "Koulu",
        "eepos_school_hint": "Osoite selaimesi osoiterivillä, kun kirjaudut koulun Eepokseen, "
                             "esimerkiksi espoo.eepos.fi (ei koulun oman verkkosivun osoite). "
                             "Koko osoitteen voi myös liittää.",
        "eepos_username": "Eepoksen käyttäjätunnus",
        "eepos_password": "Eepoksen salasana",
        "eepos_password_hint": "Se säilytetään vain palvelimellamme, jotta voimme lukea Eeposta "
                               "puolestasi.",
        "evaka": "eVaka (vapaaehtoinen)",
        "evaka_needed": "eVaka",
        "evaka_hint": "Jos lapsi on kunnallisessa päiväkodissa, joka käyttää eVakaa, kuten "
                      "Espoossa, Tampereella, Turussa ja Oulussa, kirjaudu eVakan "
                      "sähköpostiosoitteellasi ja salasanallasi, niin päiväkodin retket, "
                      "sulkupäivät, varausten määräajat ja viestit tulevat myös koosteeseen. Jätä "
                      "kentät tyhjiksi, jos et tarvitse sitä.",
        "evaka_city": "Kunta",
        "evaka_city_hint": "Valitse kuntasi listasta. Jos sitä ei ole listassa, anna sen eVakan "
                           "osoite, esimerkiksi evaka.esimerkki.fi.",
        "evaka_username": "eVakan sähköpostiosoite",
        "evaka_password": "eVakan salasana",
        "evaka_password_hint": "Jos olet kirjautunut eVakaan vain pankkitunnuksilla, aseta ensin "
                               "salasana eVakassa kohdassa Omat tiedot. Se säilytetään vain "
                               "palvelimellamme, jotta voimme lukea eVakaa puolestasi.",
        "wait": "Käyttöönotto kestää minuutin tai kaksi. Pidä tämä sivu auki, kunnes se kertoo, "
                "että olet mukana.",
        "signing_up": "Otetaan käyttöön",
        "updates": "Sivu päivittyy itsestään. Pidä se auki, kunnes se kertoo, että olet mukana; "
                   "puhelimen näyttö saa välillä sammua.",
        "queued": "Toinen perhe ottaa Parent Recapia juuri nyt käyttöön. Sinun vuorosi alkaa, kun "
                  "heidän on valmis, joten yhtäkään vaihetta ei ole vielä aloitettu.",
        "step_setup": "Parent Recapia valmistellaan sinulle",
        "step_brief": "Ensimmäinen kooste kirjoitetaan ja lähetetään",
        "wrong-password": "Wilma ei hyväksynyt käyttäjätunnusta ja salasanaa. Tarkista ne ja "
                          "yritä uudelleen.",
        "no-kids": "Tällä Wilma-tunnuksella ei ole lapsia. Kirjaudu huoltajan tunnuksella.",
        "sign-in-failed": "Wilmaan ei juuri nyt saatu yhteyttä, joten mitään ei tallennettu. "
                          "Yritä hetken päästä uudelleen.",
        "invalid-name": "Kirjoita nimi, jolla voimme kutsua teitä, esimerkiksi Virtaset. "
                        "Sähköpostiosoite ei käy.",
        "invalid-town": "Valitse kuntasi listasta.",
        "invalid-sign-in": "Täytä Wilman käyttäjätunnus ja salasana, tai lasten nimet, jos "
                           "teillä ei ole Wilmaa.",
        "need-myclub": "Ilman Wilmaa anna MyClub-kalenterin linkki tai kirjaudu Eepokseen tai "
                       "eVakaan, jotta koosteella on jotain luettavaa.",
        "invalid-recipients": "Anna vähintään yksi sähköpostiosoite muodossa nimi@esimerkki.fi.",
        "not-a-myclub-link": "{kid}: tämä ei ole MyClub-kalenterin linkki. Kopioi kalenterin "
                             "tilauslinkki MyClubista tai jätä kenttä tyhjäksi.",
        "link-failed": "{kid}: MyClub-linkki ei auennut. Tarkista se tai jätä kenttä tyhjäksi, "
                       "niin MyClub jää pois.",
        "not-a-calendar": "{kid}: linkki aukesi, mutta siinä ei ole kalenteria. Kopioi kalenterin "
                          "tilauslinkki MyClubista tai jätä kenttä tyhjäksi.",
        "same-link": "{kid}: tämä on sama linkki kuin toisella lapsella. Jokaisella lapsella on "
                     "oma MyClub-kalenterin linkkinsä.",
        "eepos-incomplete": "Täytä Eepoksen koulu, käyttäjätunnus ja salasana tai jätä ne kaikki "
                            "tyhjiksi.",
        "eepos-not-a-school": "Tämä ei ole Eepos-koulu. Anna osoitteen alku, esimerkiksi emo, kun "
                              "osoite on emo.eepos.fi.",
        "eepos-school-website": "Tämä näyttää koulun verkkosivulta. Kopioi sen sijaan osoite "
                                "selaimesta koulun Eepos-kirjautumissivulta: se päättyy "
                                "muotoon .eepos.fi, esimerkiksi espoo.eepos.fi.",
        "eepos-wrong-password": "Eepos ei hyväksynyt käyttäjätunnusta ja salasanaa. Tarkista ne "
                                "ja yritä uudelleen. Jos kirjaudut Eepokseen sähköpostilinkillä, "
                                "aseta ensin salasana koulun Eepos-kirjautumissivulla salasanan "
                                "palautuksella.",
        "eepos-changed": "Eepos vastasi tavalla, jota Parent Recap ei tunne, joten mitään ei "
                         "tallennettu. Jätä Eepos tyhjäksi, niin pääset mukaan ilman sitä, ja "
                         "kerro asiasta linkin lähettäjälle.",
        "eepos-no-answer": "Eepokseen ei juuri nyt saatu yhteyttä, joten mitään ei tallennettu. "
                           "Yritä hetken päästä uudelleen tai jätä Eepos tyhjäksi.",
        "evaka-incomplete": "Täytä eVakan kunta, sähköpostiosoite ja salasana tai jätä ne kaikki "
                            "tyhjiksi.",
        "evaka-not-a-city": "Tämä ei ole eVakaa käyttävä kunta. Valitse se listasta tai anna sen "
                            "eVakan osoite.",
        "evaka-wrong-password": "eVaka ei hyväksynyt sähköpostiosoitetta ja salasanaa. Tarkista ne "
                                "ja yritä uudelleen. Jos olet kirjautunut eVakaan vain "
                                "pankkitunnuksilla, aseta ensin salasana eVakassa kohdassa Omat "
                                "tiedot.",
        "evaka-changed": "eVaka vastasi tavalla, jota Parent Recap ei tunne, joten mitään ei "
                         "tallennettu. Jätä eVaka tyhjäksi, niin pääset mukaan ilman sitä, ja "
                         "kerro asiasta linkin lähettäjälle.",
        "evaka-no-answer": "eVakaan ei juuri nyt saatu yhteyttä, joten mitään ei tallennettu. "
                           "Yritä hetken päästä uudelleen tai jätä eVaka tyhjäksi.",
        "paste_again": "Linkkejä sekä Eepoksen ja eVakan salasanoja ei näytetä uudelleen, koska "
                       "niillä pääsee lapsen kalenteriin: anna haluamasi uudelleen.",
        "expired": "Tämä kesti liian kauan, joten mitään ei tallennettu. Kirjaudu Wilmaan "
                   "uudelleen.",
        "failed": "Meidän päässämme meni jotain vikaan, eikä mitään tallennettu. Yritä myöhemmin "
                  "uudelleen tai kerro asiasta linkin lähettäjälle.",
        "done": "Olet mukana",
        "sent": "Ensimmäinen kooste lähetettiin juuri sähköpostiisi. Tästä eteenpäin kooste tulee "
                "joka ilta kello {time}, kun on jotain uutta.",
        "junk": "Hotmail ja Outlook.com voivat laittaa ensimmäisen koosteen roskapostiin. Jos se "
                "on siellä, merkitse se kerran \"Ei roskapostia\".",
        "eepos_unmatched": "Eepoksen oppilasta {names} ei tunnistettu kenenkään lapsesi nimestä, "
                           "joten tunnit tulevat koosteeseen koko perheelle.",
        "evaka_unmatched": "eVakan lasta {names} ei tunnistettu kenenkään lapsesi nimestä, joten "
                           "päiväkodin tapahtumat tulevat koosteeseen koko perheelle.",
    },
    "zh": {
        "title": "开始使用 Parent Recap",
        "intro": "Parent Recap 每天晚上读取孩子的 Wilma，把重要的内容整理成一份简短的摘要发到你的邮箱。"
                 "在这里登录一次 Wilma，其余的交给我们。",
        "name": "我们怎么称呼你？",
        "name_hint": "你家的称呼，例如「王家」或「小雨妈妈」。只有 Parent Recap 的维护者能看到，用来知道是谁开通的。",
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
        "no_wilma": "我们没有 Wilma",
        "no_wilma_hint": "如果孩子的学校不用 Wilma，把上面 Wilma 的几栏留空，在这里写下每个孩子的名字。"
                         "摘要会读取孩子俱乐部的 MyClub 日历、音乐学校的 Eepos 或幼儿园的 eVaka，下一步填写。",
        "kid": "孩子的名字",
        "myclub_needed": "MyClub",
        "submit": "继续",
        "myclub": "MyClub（可选）",
        "myclub_hint": "如果孩子的俱乐部用 MyClub，把孩子在 MyClub 里的日历链接粘贴到这里（以 webcal:// "
                       "或 https:// 开头），训练和比赛也会出现在摘要里。不用 MyClub？留空，直接点“完成”。",
        "myclub_needed_hint": "如果孩子的俱乐部用 MyClub，把孩子在 MyClub 里的日历链接粘贴到这里（以 "
                              "webcal:// 或 https:// 开头），训练和比赛也会出现在摘要里。不需要就留空。",
        "myclub_done": "完成",
        "another_club": "再加一个俱乐部",
        "another_club_link": "{kid}：另一个俱乐部的链接",
        "eepos": "Eepos（可选）",
        "eepos_needed": "Eepos",
        "eepos_hint": "如果孩子在使用 Eepos 的音乐或艺术学校上课，用你的 Eepos 用户名和密码登录，"
                      "课程、音乐会和假期也会出现在摘要里。不需要就留空。",
        "eepos_school": "学校",
        "eepos_school_hint": "登录 Eepos 时浏览器地址栏里的网址，例如 espoo.eepos.fi（不是学校官网）。"
                             "直接粘贴整个网址也可以。",
        "eepos_username": "Eepos 用户名",
        "eepos_password": "Eepos 密码",
        "eepos_password_hint": "它只保存在我们的服务器上，用来替你读取 Eepos。",
        "evaka": "eVaka（可选）",
        "evaka_needed": "eVaka",
        "evaka_hint": "如果孩子上的公立幼儿园用 eVaka（Espoo、Tampere、Turku、Oulu 等城市都在用），"
                      "用你在 eVaka 的邮箱和密码登录，幼儿园的出游、闭园日、预约截止时间和消息也会出现在摘要里。"
                      "不需要就留空。",
        "evaka_city": "城市",
        "evaka_city_hint": "从列表里选择你的城市。不在列表里的，填它的 eVaka 网址，例如 evaka.example.fi。",
        "evaka_username": "eVaka 邮箱",
        "evaka_password": "eVaka 密码",
        "evaka_password_hint": "如果你一直只用银行账号登录 eVaka，请先在 eVaka 的“个人信息”（Omat tiedot）"
                               "里设置一个密码。它只保存在我们的服务器上，用来替你读取 eVaka。",
        "wait": "开通需要一两分钟。请保持这个页面打开，直到它显示开通成功。",
        "signing_up": "正在开通",
        "updates": "这个页面会自动更新。请保持它打开，直到它显示开通成功；中途手机屏幕熄灭也没关系。",
        "queued": "另一个家庭正在开通。等他们完成后才轮到你，所以你的步骤都还没有开始。",
        "step_setup": "为你准备 Parent Recap",
        "step_brief": "撰写并发送第一份摘要",
        "wrong-password": "Wilma 没有接受这个用户名和密码。请检查后再试一次。",
        "no-kids": "这个 Wilma 账号下没有孩子。请用家长（监护人）的账号登录。",
        "sign-in-failed": "现在连不上 Wilma，所以什么都没有保存。请过一会儿再试。",
        "invalid-name": "请填写一个我们可以称呼你的名字，例如「王家」。不能是邮箱地址。",
        "invalid-town": "请从列表里选择你的城市。",
        "invalid-sign-in": "请填写 Wilma 用户名和密码；没有 Wilma 的话，填写孩子的名字。",
        "need-myclub": "没有 Wilma 时，请填一个 MyClub 日历链接，或者登录 Eepos 或 eVaka，这样摘要才有内容可读。",
        "invalid-recipients": "请至少填写一个邮箱地址，格式如 name@example.com。",
        "not-a-myclub-link": "{kid}：这不是 MyClub 日历链接。请在 MyClub 里复制日历的订阅链接，或者留空。",
        "link-failed": "{kid}：这个 MyClub 链接打不开。请检查一下，或者留空跳过 MyClub。",
        "not-a-calendar": "{kid}：链接能打开，但里面没有日历。请在 MyClub 里复制日历的订阅链接，或者留空。",
        "same-link": "{kid}：这个链接和另一个孩子的一样。每个孩子都有自己的 MyClub 日历链接。",
        "eepos-incomplete": "Eepos 的学校、用户名和密码请都填上，或者都留空。",
        "eepos-not-a-school": "这不是 Eepos 学校。请填网址的开头部分，例如 emo.eepos.fi 就填 emo。",
        "eepos-school-website": "这看起来是学校官网的网址。请改为复制学校 Eepos 登录页在浏览器地址栏里的"
                                "网址，它以 .eepos.fi 结尾，例如 espoo.eepos.fi。",
        "eepos-wrong-password": "Eepos 没有接受这个用户名和密码。请检查后再试一次。如果你平时通过邮件里的"
                                "链接登录 Eepos，请先在学校的 Eepos 登录页用“忘记密码”设置一个密码。",
        "eepos-changed": "Eepos 的回应 Parent Recap 看不懂，所以什么都没有保存。可以把 Eepos 留空先开通，"
                         "并告诉发给你这个链接的人。",
        "eepos-no-answer": "现在连不上 Eepos，所以什么都没有保存。请过一会儿再试，或者把 Eepos 留空。",
        "evaka-incomplete": "eVaka 的城市、邮箱和密码请都填上，或者都留空。",
        "evaka-not-a-city": "这个城市不在 eVaka 上。请从列表里选择，或者填它的 eVaka 网址。",
        "evaka-wrong-password": "eVaka 没有接受这个邮箱和密码。请检查后再试一次。如果你一直只用银行账号登录 "
                                "eVaka，请先在 eVaka 的“个人信息”（Omat tiedot）里设置一个密码。",
        "evaka-changed": "eVaka 的回应 Parent Recap 看不懂，所以什么都没有保存。可以把 eVaka 留空先开通，"
                         "并告诉发给你这个链接的人。",
        "evaka-no-answer": "现在连不上 eVaka，所以什么都没有保存。请过一会儿再试，或者把 eVaka 留空。",
        "paste_again": "链接以及 Eepos 和 eVaka 的密码不会再显示，因为它们能打开孩子的日历：请把需要的再填一次。",
        "expired": "等待时间太长，什么都没有保存。请重新登录 Wilma。",
        "failed": "我们这边出了问题，什么都没有保存。请稍后再试，或告诉发给你这个链接的人。",
        "done": "开通成功",
        "sent": "第一份摘要刚刚已经发到你的邮箱。之后每天晚上 {time}，有新消息时就会发一份摘要。",
        "junk": "Hotmail 和 Outlook.com 可能会把第一份摘要放进垃圾邮件。如果在那里找到它，请把它标记为“非垃圾邮件”一次。",
        "eepos_unmatched": "Eepos 上的 {names} 和孩子的名字对不上，所以他们的课程会作为全家的日程出现在摘要里。",
        "evaka_unmatched": "eVaka 上的 {names} 和孩子的名字对不上，所以幼儿园的日程会作为全家的日程出现在摘要里。",
    },
}
STYLE = """
body{font:16px/1.45 system-ui,-apple-system,sans-serif;margin:0;background:#f6f5f2;color:#1d1d1b}
main{max-width:34rem;margin:0 auto;padding:1rem 1rem 3rem}
h1{font-size:1.4rem;margin:.5rem 0 1rem}h2{font-size:1.1rem;margin:1.5rem 0 .5rem}
nav{display:flex;gap:1rem;justify-content:flex-end}nav a{color:#1d1d1b}nav b{font-weight:600}
label{display:block;margin:.75rem 0 .25rem;font-weight:600}
summary{margin:1rem 0 .25rem;font-weight:600;cursor:pointer}
input,select{box-sizing:border-box;width:100%;font:inherit;padding:.6rem;border:1px solid #b9b6ad;
border-radius:.4rem;background:#fff}
.row{display:flex;gap:.5rem}.row input{flex:3}.row select{flex:1;min-width:6rem}
.hint{color:#5b5a55;font-size:.9rem;margin:.25rem 0}
.error{background:#fde8e4;border:1px solid #e0a197;border-radius:.4rem;padding:.75rem}
ol{list-style:none;padding:0}li{margin:.5rem 0}
button{margin-top:1.5rem;width:100%;font:inherit;font-weight:600;padding:.8rem;border:0;
border-radius:.4rem;background:#2d5c4f;color:#fff}
"""
# The Sources the second step of making a Household connects, in the order it names them, and how
# each language says it connects them: "Connecting Wilma, Eepos and eVaka" (#790).
CONNECTS = {"wilma": "Wilma", "myclub": "MyClub", "eepos": "Eepos", "evaka": "eVaka"}
CONNECTING = {"en": ("Connecting {}", ", ", " and "), "fi": ("{} yhdistetään", ", ", " ja "),
              "zh": ("连接 {}", "、", " 和 ")}


def _connecting() -> None:
    """Puts in TEXT the step that connects each set of CONNECTS, as `step_wilma_eepos`."""
    for language, (said, comma, also) in CONNECTING.items():
        for n in range(1, 1 << len(CONNECTS)):
            keys = [k for i, k in enumerate(CONNECTS) if n >> i & 1]
            names = [CONNECTS[k] for k in keys]
            TEXT[language]["step_" + "_".join(keys)] = said.format(
                comma.join(names[:-1]) + also + names[-1] if len(names) > 1 else names[0])


_connecting()
# eVaka's cities as a family writes them, for the city field to offer: collectors/evaka.CITIES's.
EVAKA_CITIES = ("Espoo", "Tampere", "Turku", "Oulu", "Hämeenkyrö", "Orivesi", "Ylöjärvi",
                "Pirkkala", "Nokia", "Lempäälä", "Vesilahti", "Kangasala")
MAX_RECIPIENTS = 10  # the Brief relay's, for one message
# What making a Household does, in the order the server does it, in words a parent understands.
STEPS = ("step_setup", "step_wilma", "step_brief")
NO_WILMA_STEPS = ("step_setup", "step_myclub", "step_brief")  # a Household without Wilma (#760)
MAX_KIDS = 6  # the names a family without Wilma may give
MAX_CLUBS = 4  # the MyClub links one Kid may give, one per club (#810)
MAX_NAME = 60  # what the family asks to be called (#814)
REFRESH_SECONDS = 3


def steps(wilma: bool, myclub: bool, eepos: bool, evaka: bool = False) -> tuple[str, ...]:
    """The steps of making a Household, the second named after the Sources it connects: Wilma,
    or without it MyClub, and Eepos (#769) and eVaka (#790) when given."""
    connects = [k for k, given in (("wilma", wilma), ("myclub", myclub and not wilma),
                                   ("eepos", eepos), ("evaka", evaka)) if given] or ["myclub"]
    return ("step_setup", "step_" + "_".join(connects), "step_brief")


@dataclass
class Typed:
    """What the family typed, to show again when the page comes back with why: never the
    password."""
    town: str = ""
    username: str = ""
    recipients: list[Recipient] = field(default_factory=list)
    kids: list[str] = field(default_factory=list)  # the names given in place of Wilma
    name: str = ""  # what the family asks to be called (#814)


def form(language: str, typed: Typed, towns: list[str], error: str | None = None) -> str:
    t = TEXT[language]
    rows = [*typed.recipients[:MAX_RECIPIENTS - 1], Recipient("", language)]
    recipients = "".join(_recipient(i, r, t, last=i == len(rows) - 1) for i, r in enumerate(rows))
    options = "".join(f"<option value=\"{escape(town)}\">" for town in towns)
    names = [*typed.kids[:MAX_KIDS], *[""] * (MAX_KIDS - len(typed.kids[:MAX_KIDS]))]
    kids = "".join(f"""<label for="k{i}">{escape(t['kid'])}</label>
<input id="k{i}" name="k{i}" autocomplete="off" value="{escape(name)}">
""" for i, name in enumerate(names))
    return _page(language, t["title"], f"""
<p>{escape(t['intro'])}</p>
{f'<p class="error" role="alert">{escape(t[error])}</p>' if error else ''}
<form method="post" autocomplete="on">
<input type="hidden" name="lang" value="{language}">
<label for="name">{escape(t['name'])}</label>
<input id="name" name="name" autocomplete="off" maxlength="{MAX_NAME}" required
 value="{escape(typed.name)}">
<p class="hint">{escape(t['name_hint'])}</p>
<h2>{escape(t['wilma'])}</h2>
<label for="town">{escape(t['town'])}</label>
<input id="town" name="town" list="towns" autocomplete="off" value="{escape(typed.town)}">
<datalist id="towns">{options}</datalist>
<p class="hint">{escape(t['town_hint'])}</p>
<label for="username">{escape(t['username'])}</label>
<input id="username" name="username" autocomplete="username" autocapitalize="none"
 spellcheck="false" value="{escape(typed.username)}">
<label for="password">{escape(t['password'])}</label>
<input id="password" name="password" type="password" autocomplete="current-password">
<p class="hint">{escape(t['password_hint'])}</p>
<details{' open' if typed.kids else ''}><summary>{escape(t['no_wilma'])}</summary>
<p class="hint">{escape(t['no_wilma_hint'])}</p>
{kids}</details>
<h2>{escape(t['recipients'])}</h2>
<p class="hint">{escape(t['recipients_hint'])}</p>
{recipients}
<button type="submit">{escape(t['submit'])}</button>
</form>""")


def myclub(language: str, kids: list[str], error: str | None = None,
           kid: str | None = None, wilma: bool = True, eepos: tuple[str, str] = ("", ""),
           evaka: tuple[str, str] = ("", "")) -> str:
    """The second step, once Wilma, or the family without it, has said who the Kids are: MyClub
    calendar links for each, one per club (#810), an Eepos sign-in (#769) and an eVaka sign-in (#790), each of which may
    be left empty, though not all without Wilma. A link is never shown back, as the passwords
    aren't (ADR 0007); the Eepos school and username typed, `eepos`, and the eVaka city and email,
    `evaka`, are."""
    t = TEXT[language]
    school, username = eepos
    city, email = evaka
    cities = "".join(f"<option value=\"{escape(c)}\">" for c in EVAKA_CITIES)
    fields = "".join(_links(i, name, t) for i, name in enumerate(kids))
    alert = (f'<p class="error" role="alert">{escape(t[error].format(kid=kid))}</p>'
             f'<p class="hint">{escape(t["paste_again"])}</p>') if error else ""
    return _page(language, t["title"], f"""
{alert}
<form method="post" autocomplete="off">
<input type="hidden" name="lang" value="{language}">
<input type="hidden" name="step" value="myclub">
<h2>{escape(t['myclub' if wilma else 'myclub_needed'])}</h2>
<p class="hint">{escape(t['myclub_hint' if wilma else 'myclub_needed_hint'])}</p>
{fields}
<h2>{escape(t['eepos' if wilma else 'eepos_needed'])}</h2>
<p class="hint">{escape(t['eepos_hint'])}</p>
<label for="eepos_school">{escape(t['eepos_school'])}</label>
<input id="eepos_school" name="eepos_school" autocomplete="off" autocapitalize="none"
 spellcheck="false" value="{escape(school)}">
<p class="hint">{escape(t['eepos_school_hint'])}</p>
<label for="eepos_username">{escape(t['eepos_username'])}</label>
<input id="eepos_username" name="eepos_username" autocomplete="off" autocapitalize="none"
 spellcheck="false" value="{escape(username)}">
<label for="eepos_password">{escape(t['eepos_password'])}</label>
<input id="eepos_password" name="eepos_password" type="password" autocomplete="off">
<p class="hint">{escape(t['eepos_password_hint'])}</p>
<h2>{escape(t['evaka' if wilma else 'evaka_needed'])}</h2>
<p class="hint">{escape(t['evaka_hint'])}</p>
<label for="evaka_city">{escape(t['evaka_city'])}</label>
<input id="evaka_city" name="evaka_city" list="evaka_cities" autocomplete="off"
 value="{escape(city)}">
<datalist id="evaka_cities">{cities}</datalist>
<p class="hint">{escape(t['evaka_city_hint'])}</p>
<label for="evaka_username">{escape(t['evaka_username'])}</label>
<input id="evaka_username" name="evaka_username" inputmode="email" autocomplete="off"
 autocapitalize="none" spellcheck="false" value="{escape(email)}">
<label for="evaka_password">{escape(t['evaka_password'])}</label>
<input id="evaka_password" name="evaka_password" type="password" autocomplete="off">
<p class="hint">{escape(t['evaka_password_hint'])}</p>
<button type="submit">{escape(t['myclub_done'])}</button>
<p class="hint">{escape(t['wait'])}</p>
</form>""", languages=False)


def _links(i: int, kid: str, t: dict[str, str]) -> str:
    """Kid `i`'s MyClub link fields: one, and under "Add another club" one for each other club
    (#810), opened with no script, so the Content-Security-Policy holds."""
    def field(name: str, label: str) -> str:
        return f"""<label for="{name}">{escape(label)}</label>
<input id="{name}" name="{name}" inputmode="url" autocomplete="off" autocapitalize="none"
 spellcheck="false">
"""
    more = "".join(field(f"m{i}_{n}", t["another_club_link"].format(kid=kid))
                   for n in range(1, MAX_CLUBS))
    return (field(f"m{i}", kid)
            + f"<details><summary>{escape(t['another_club'])}</summary>\n{more}</details>\n")


def _recipient(i: int, r: Recipient, t: dict[str, str], last: bool) -> str:
    label = t["another"] if last else t["address"]
    choices = "".join(f"<option value=\"{code}\"{' selected' if code == r.language else ''}>"
                      f"{NAMES[code]}</option>" for code in LANGUAGES)
    return f"""<label for="r{i}">{escape(label)}</label>
<div class="row"><input id="r{i}" name="r{i}" type="email" autocomplete="email"
 value="{escape(r.address)}"{' required' if i == 0 else ''}>
<select name="r{i}_language" aria-label="{escape(t['language'])}">{choices}</select></div>
"""


def progress(language: str, ended: int, waiting: bool, steps: tuple[str, ...] = STEPS) -> str:
    """The steps while the Household is made, `ended` of them over, or none begun while it
    `waiting` behind another family's; asked again by itself until the page says how it ended."""
    t = TEXT[language]
    queued = f'<p>{escape(t["queued"])}</p>' if waiting else ""
    return _page(language, t["signing_up"], f"""
{queued}{_steps(t, ended, None if waiting else "⏳", steps)}
<p class="hint">{escape(t['updates'])}</p>""", languages=False, refresh=True)


def done(language: str, time: str, junk: bool, steps: tuple[str, ...] = STEPS,
         unmatched: list[str] | None = None, evaka_unmatched: list[str] | None = None) -> str:
    """Signed up: the first Brief sent, and when the next ones come. `junk` when a Recipient is
    on Hotmail or Outlook.com, which may file a new sender's first mail as Junk; `unmatched`, the
    Eepos students who are none of the Kids, whose lessons are the whole Household's, and
    `evaka_unmatched` the eVaka children who are none of them."""
    t = TEXT[language]
    hint = f'\n<p class="hint">{escape(t["junk"])}</p>' if junk else ""
    for key, names in (("eepos_unmatched", unmatched), ("evaka_unmatched", evaka_unmatched)):
        if names:
            said = t[key].format(names=", ".join(names))
            hint += f'\n<p class="hint">{escape(said)}</p>'
    return _page(language, t["done"], f"""
{_steps(t, len(steps), None, steps)}
<p>{escape(t['sent'].format(time=time))}</p>{hint}""", languages=False)


def failed(language: str, ended: int | None = None, steps: tuple[str, ...] = STEPS) -> str:
    """Nothing kept: with the steps, the one after the `ended` ones marked as the one that
    failed, when it was a step of making the Household."""
    t = TEXT[language]
    shown = _steps(t, ended, "❌", steps) if ended is not None else ""
    return _page(language, t["title"],
                 f'{shown}<p class="error" role="alert">{escape(t["failed"])}</p>',
                 languages=False)


def _steps(t: dict[str, str], ended: int, now: str | None, steps: tuple[str, ...]) -> str:
    """The steps, the first `ended` marked ✅ and the next with `now`, when one goes on."""
    marks = ["✅"] * ended + ([now] if now and ended < len(steps) else [])
    marks += ["⬜"] * (len(steps) - len(marks))
    return "<ol>" + "".join(f"<li>{mark} {escape(t[step])}</li>"
                            for mark, step in zip(marks, steps)) + "</ol>"


def _page(language: str, title: str, body: str, languages: bool = True,
          refresh: bool = False) -> str:
    nav = "".join(f"<b>{NAMES[code]}</b>" if code == language
                  else f"<a href=\"?lang={code}\" hreflang=\"{code}\">{NAMES[code]}</a>"
                  for code in LANGUAGES) if languages else ""
    again = f'\n<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' if refresh else ""
    return f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">{again}
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
