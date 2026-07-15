-- 0002: unify nutrient keys across sources.
--
-- The Phase 1 bulk import auto-slugged FDC names that embed element symbols
-- ("Potassium, K" -> potassium_k_mg), while OFF imports use clean keys
-- (potassium_mg). FDC_CANONICAL in app/normalize.py now maps these at import
-- time; this migration renames rows loaded before the map existed.
--
-- Collision-safe: if a food somehow already has the canonical key, the old
-- row is dropped instead of renamed (PK is food_id, nutrient_key).

DO $$
DECLARE
  pair text[];
  renames text[][] := ARRAY[
    ['potassium_k_mg',                    'potassium_mg'],
    ['magnesium_mg_mg',                   'magnesium_mg'],
    ['calcium_ca_mg',                     'calcium_mg'],
    ['iron_fe_mg',                        'iron_mg'],
    ['zinc_zn_mg',                        'zinc_mg'],
    ['phosphorus_p_mg',                   'phosphorus_mg'],
    ['copper_cu_mg',                      'copper_mg'],
    ['manganese_mn_mg',                   'manganese_mg'],
    ['selenium_se_ug',                    'selenium_ug'],
    ['vitamin_a_rae_ug',                  'vitamin_a_ug'],
    ['vitamin_e_alpha_tocopherol_mg',     'vitamin_e_mg'],
    ['vitamin_d_d2_d3_ug',                'vitamin_d_ug'],
    ['vitamin_c_total_ascorbic_acid_mg',  'vitamin_c_mg'],
    ['sugars_total_including_nlea_g',     'sugars_total_g'],
    ['alcohol_ethyl_g',                   'alcohol_g']
  ];
BEGIN
  FOREACH pair SLICE 1 IN ARRAY renames LOOP
    UPDATE food_log.nutrients n
       SET nutrient_key = pair[2]
     WHERE n.nutrient_key = pair[1]
       AND NOT EXISTS (
         SELECT 1 FROM food_log.nutrients x
          WHERE x.food_id = n.food_id AND x.nutrient_key = pair[2]
       );
    DELETE FROM food_log.nutrients WHERE nutrient_key = pair[1];
  END LOOP;
END $$;
