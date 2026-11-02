mkdir healthcare && cd healthcare

apple-code solve "Write three small Python files that use the pydicom package. File 1 is patient.py with patient_label(ds) -> str that takes a pydicom Dataset and returns 'family, given (sex)' from ds.PatientName and ds.PatientSex, for example from pydicom.dataset import Dataset then ds = Dataset() then ds.PatientName = 'Doe^John' then ds.PatientSex = 'M' then patient_label(ds) == 'Doe, John (M)', and its tests are in tests/test_patient.py. File 2 is age.py with age_on(birth_date: str, on_date: str) -> int where both dates are DICOM dates in the form YYYYMMDD, that returns the whole years completed on on_date, for example age_on('19800615', '20200614') == 39, age_on('19800615', '20200615') == 40, age_on('20200229', '20210228') == 0, age_on('20200229', '20210301') == 1, and its tests are in tests/test_age.py. File 3 is summary.py with summary(ds, on_date: str) -> str that uses patient_label from patient.py and age_on from age.py with ds.PatientBirthDate and returns 'label, N years', for example from pydicom.dataset import Dataset then ds = Dataset() then ds.PatientName = 'Doe^John' then ds.PatientSex = 'M' then ds.PatientBirthDate = '19800615' then summary(ds, '20200615') == 'Doe, John (M), 40 years', and its tests are in tests/test_summary.py."

apple-code test tests

cat > check.py <<'EOF'
from pydicom.dataset import Dataset
from patient import patient_label
from age import age_on
from summary import summary

ds = Dataset()
ds.PatientName = "Doe^John"
ds.PatientSex = "M"
ds.PatientBirthDate = "19800615"
assert patient_label(ds) == "Doe, John (M)"
assert age_on("19800615", "20200614") == 39 and age_on("19800615", "20200615") == 40 and age_on("20200229", "20210228") == 0 and age_on("20200229", "20210301") == 1
assert summary(ds, "20200615") == "Doe, John (M), 40 years" and summary(ds, "20200614") == "Doe, John (M), 39 years"
print("PROMPT6 independent check: all passed")
EOF

python3 check.py
apple-code recap
