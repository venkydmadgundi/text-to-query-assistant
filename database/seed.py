from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database.schema import Employee


SAMPLE_EMPLOYEES = (
    (1, "Priya", "Sharma", "priya.sharma@example.com", "HR", "HR Director", "145000.00", "2017-04-10", "Mumbai", None),
    (2, "Arjun", "Mehta", "arjun.mehta@example.com", "Engineering", "Engineering Director", "180000.00", "2016-08-22", "Bengaluru", None),
    (3, "Neha", "Patel", "neha.patel@example.com", "Sales", "Sales Manager", "135000.00", "2018-02-05", "Pune", None),
    (4, "Rahul", "Nair", "rahul.nair@example.com", "Finance", "Finance Manager", "142000.00", "2017-11-13", "Chennai", None),
    (5, "Kavya", "Iyer", "kavya.iyer@example.com", "HR", "HR Manager", "112000.00", "2020-06-15", "Hyderabad", 1),
    (6, "Amit", "Joshi", "amit.joshi@example.com", "Engineering", "Senior Engineer", "118000.00", "2021-03-08", "Bengaluru", 2),
    (7, "Ananya", "Rao", "ananya.rao@example.com", "Engineering", "Software Engineer", "96000.00", "2023-01-16", "Pune", 2),
    (8, "Rohan", "Gupta", "rohan.gupta@example.com", "Engineering", "Data Analyst", "91000.00", "2022-05-23", "Delhi", 2),
    (9, "Sneha", "Desai", "sneha.desai@example.com", "Sales", "Sales Executive", "82000.00", "2024-07-01", "Mumbai", 3),
    (10, "Vikram", "Shah", "vikram.shah@example.com", "Sales", "Senior Sales Executive", "105000.00", "2021-09-20", "Hyderabad", 3),
    (11, "Meera", "Kulkarni", "meera.kulkarni@example.com", "Marketing", "Marketing Manager", "121000.00", "2019-04-02", "Pune", 1),
    (12, "Karan", "Verma", "karan.verma@example.com", "Marketing", "Content Specialist", "74000.00", "2025-02-10", "Delhi", 11),
    (13, "Ishita", "Banerjee", "ishita.banerjee@example.com", "Finance", "Financial Analyst", "87000.00", "2022-08-18", "Chennai", 4),
    (14, "Dev", "Malhotra", "dev.malhotra@example.com", "Finance", "Accountant", "78000.00", "2023-06-12", "Mumbai", 4),
    (15, "Anil", "Kumar", "anil.kumar@example.com", "Operations", "Operations Manager", "119000.00", "2018-10-29", "Kolkata", 1),
    (16, "Fatima", "Khan", "fatima.khan@example.com", "Operations", "Operations Coordinator", "66000.00", "2024-03-11", "Bengaluru", 15),
    (17, "Nikhil", "Reddy", "nikhil.reddy@example.com", "Support", "Support Team Lead", "94000.00", "2020-12-07", "Hyderabad", 2),
    (18, "Pooja", "Menon", "pooja.menon@example.com", "Support", "Support Analyst", "72000.00", "2023-09-04", "Kochi", 17),
    (19, "Aditya", "Kapoor", "aditya.kapoor@example.com", "HR", "HR Analyst", "76000.00", "2025-01-20", "Mumbai", 5),
    (20, "Sanjana", "Bose", "sanjana.bose@example.com", "Engineering", "Engineering Manager", "154000.00", "2019-07-15", "Delhi", 2),
    (21, "Yash", "Tiwari", "yash.tiwari@example.com", "Engineering", "QA Engineer", "88000.00", "2020-02-03", "Chennai", 20),
    (22, "Varsha", "Pillai", "varsha.pillai@example.com", "Sales", "Account Manager", "126000.00", "2017-05-19", "Pune", 3),
    (23, "Deepak", "Jain", "deepak.jain@example.com", "Marketing", "Marketing Analyst", "83000.00", "2022-10-24", "Jaipur", 11),
    (24, "Ayesha", "Siddiqui", "ayesha.siddiqui@example.com", "Finance", "Payroll Specialist", "89000.00", "2021-01-11", "Hyderabad", 4),
    (25, "Sameer", "Yadav", "sameer.yadav@example.com", "Operations", "Operations Analyst", "93000.00", "2023-04-17", "Kolkata", 15),
    (26, "Tara", "Choudhary", "tara.choudhary@example.com", "HR", "Recruiter", "69000.00", "2024-11-04", "Delhi", 5),
    (27, "Suresh", "Bhat", "suresh.bhat@example.com", "Support", "Support Specialist", "68000.00", "2018-06-25", "Pune", 17),
    (28, "Ritu", "Deshmukh", "ritu.deshmukh@example.com", "Engineering", "AI Engineer", "135000.00", "2025-05-12", "Bengaluru", 20),
)


def seed_employees(engine: Engine) -> int:
    """Insert the sample set only when the employees table is empty."""
    with Session(engine) as session:
        existing_count = session.scalar(select(func.count()).select_from(Employee))
        if existing_count:
            return 0

        generated_ids: dict[int, int] = {}
        for (
            sample_id,
            first_name,
            last_name,
            email,
            department,
            designation,
            salary,
            joining_date,
            city,
            manager_sample_id,
        ) in SAMPLE_EMPLOYEES:
            employee = Employee(
                first_name=first_name,
                last_name=last_name,
                email=email,
                department=department,
                designation=designation,
                salary=Decimal(salary),
                joining_date=date.fromisoformat(joining_date),
                city=city,
                manager_id=generated_ids.get(manager_sample_id),
            )
            session.add(employee)
            session.flush()
            generated_ids[sample_id] = employee.id

        session.commit()
        return len(SAMPLE_EMPLOYEES)
