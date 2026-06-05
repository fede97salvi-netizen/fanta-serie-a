-- Fix pronosticabile per partite con pronostici esistenti
-- 80 partite dalla G3 alla G28

UPDATE partite SET pronosticabile = TRUE WHERE id IN (431, 436, 438, 439, 442, 447, 451, 456, 458, 459, 463, 468, 472, 477, 478, 479, 483, 488, 490, 496, 498, 501, 506, 508, 509, 513, 518, 522, 526, 528, 529, 530, 533, 534, 540, 541, 546, 552, 554, 558, 559, 561, 564, 565, 570, 571, 575, 580, 581, 585, 589, 592, 595, 601, 605, 606, 609, 610, 615, 620, 625, 628, 629, 633, 634, 639, 645, 646, 652, 655, 656, 661, 663, 667, 669, 673, 674, 682, 684, 687);

-- Verifica
SELECT giornata, COUNT(*) as pronosticabili FROM partite WHERE pronosticabile = TRUE GROUP BY giornata ORDER BY giornata;