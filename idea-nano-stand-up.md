In our infrastructure CF nano (https://nano.ceitec.cz/) we have booking system accessable by api ('bookig-api-json' folder symlink). In api folder there si also a script to use for search in the booking system, you can utilise that. However there are secrets (api key), which has to stay unpublished. The directory do not have github repo linked, so you can link it to this repo: git@github.com:TomasSpusta/nano-stand-up.git.

There are various equipments (like microscopes, analyzators, etc) for users to use. All of the equipments have their guarantors (admins). We are going to create something like stand-up for guarantors. 

Lets assume you are guarantor of the secondary electron microscope or chemical laboratory and you want to have brief who will have what experiment on your machine that week. Imagine you come to work on monday and at 0800 you will have complex brief about the situation about your equipment. Ideally stand-up will be automatic as an email. 

We will need to think about how to send email, how to scrape reservations for specific equipment, how to read plan of the reservation. 

Also there need to be some safety features - who can and who cannot see the standups -> I am thinking about simple web app with name/email of guarantor and name of instrument -> if they are not same in the booking system, we will not allow to send email. If they are matching, it will be something like newsletter subscribe site. 



