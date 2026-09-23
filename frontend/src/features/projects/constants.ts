/** Cancer types offered for a project, grouped by organ system. Anything else can be typed in. */
export const CANCER_TYPE_GROUPS: { group: string; types: string[] }[] = [
  {
    group: "Breast",
    types: ["Breast Cancer", "Invasive Ductal Carcinoma", "Invasive Lobular Carcinoma", "Ductal Carcinoma In Situ (DCIS)", "Triple-Negative Breast Cancer"],
  },
  {
    group: "Lung & thoracic",
    types: ["Lung Adenocarcinoma", "Lung Squamous Cell Carcinoma", "Small Cell Lung Cancer", "Mesothelioma", "Thymoma / Thymic Carcinoma"],
  },
  {
    group: "Gastrointestinal",
    types: [
      "Esophageal Cancer",
      "Gastric (Stomach) Cancer",
      "Colorectal Cancer",
      "Colon Adenocarcinoma",
      "Rectal Cancer",
      "Anal Cancer",
      "Small Intestine Cancer",
      "Appendix Cancer",
      "Gastrointestinal Stromal Tumor (GIST)",
      "Neuroendocrine Tumor",
    ],
  },
  {
    group: "Liver, bile duct & pancreas",
    types: ["Hepatocellular Carcinoma", "Cholangiocarcinoma", "Gallbladder Cancer", "Pancreatic Ductal Adenocarcinoma", "Pancreatic Neuroendocrine Tumor"],
  },
  {
    group: "Genitourinary",
    types: [
      "Prostate Adenocarcinoma",
      "Clear Cell Renal Cell Carcinoma",
      "Papillary Renal Cell Carcinoma",
      "Chromophobe Renal Cell Carcinoma",
      "Bladder (Urothelial) Carcinoma",
      "Upper Tract Urothelial Carcinoma",
      "Testicular Germ Cell Tumor",
      "Penile Cancer",
      "Wilms Tumor",
    ],
  },
  {
    group: "Gynecologic",
    types: ["Ovarian Cancer", "High-Grade Serous Carcinoma", "Endometrial Carcinoma", "Cervical Cancer", "Uterine Sarcoma", "Vulvar Cancer", "Vaginal Cancer"],
  },
  {
    group: "Head & neck",
    types: [
      "Head & Neck Squamous Cell Carcinoma",
      "Oral Cavity Cancer",
      "Oropharyngeal Cancer",
      "Nasopharyngeal Carcinoma",
      "Laryngeal Cancer",
      "Salivary Gland Cancer",
      "Papillary Thyroid Carcinoma",
      "Follicular Thyroid Carcinoma",
      "Medullary Thyroid Carcinoma",
      "Anaplastic Thyroid Carcinoma",
    ],
  },
  {
    group: "Brain & nervous system",
    types: ["Glioblastoma", "Low-Grade Glioma", "Astrocytoma", "Oligodendroglioma", "Meningioma", "Medulloblastoma", "Ependymoma", "Pituitary Tumor", "Neuroblastoma"],
  },
  {
    group: "Skin & eye",
    types: ["Melanoma", "Basal Cell Carcinoma", "Cutaneous Squamous Cell Carcinoma", "Merkel Cell Carcinoma", "Uveal Melanoma", "Retinoblastoma"],
  },
  {
    group: "Blood & lymphatic",
    types: [
      "Hodgkin Lymphoma",
      "Diffuse Large B-Cell Lymphoma",
      "Follicular Lymphoma",
      "Non-Hodgkin Lymphoma (other)",
      "Multiple Myeloma",
      "Acute Myeloid Leukemia",
      "Acute Lymphoblastic Leukemia",
      "Chronic Lymphocytic Leukemia",
      "Chronic Myeloid Leukemia",
      "Myelodysplastic Syndrome",
    ],
  },
  {
    group: "Bone & soft tissue",
    types: ["Osteosarcoma", "Ewing Sarcoma", "Chondrosarcoma", "Liposarcoma", "Leiomyosarcoma", "Rhabdomyosarcoma", "Soft Tissue Sarcoma"],
  },
  {
    group: "Endocrine & other",
    types: ["Adrenocortical Carcinoma", "Pheochromocytoma / Paraganglioma", "Cancer of Unknown Primary"],
  },
];

export const CANCER_TYPES = CANCER_TYPE_GROUPS.flatMap((g) => g.types);

/** Drawing tools a project can enable (Select is always available). Order is the toolbar order. */
export const MVP_TOOLS = [
  { id: "point", label: "Point", icon: "control_point" },
  { id: "line", label: "Line", icon: "horizontal_rule" },
  { id: "freehand_line", label: "Freehand Line", icon: "gesture" },
  { id: "rectangle", label: "Rectangle", icon: "crop_square" },
  { id: "circle", label: "Circle", icon: "radio_button_unchecked" },
  { id: "polygon", label: "Polygon", icon: "pentagon" },
  { id: "freehand", label: "Freehand Polygon", icon: "draw" },
];
