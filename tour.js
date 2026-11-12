const EYE = 1.55;
const TOUR = [
 {
  "id": "garden",
  "name": "Garden",
  "area": "Outside",
  "pos": [
   -16,
   -21,
   1.55
  ],
  "links": [
   "pool"
  ]
 },
 {
  "id": "pool",
  "name": "Pool deck",
  "area": "Outside",
  "pos": [
   -5,
   -13.1,
   1.7
  ],
  "links": [
   "garden",
   "fire",
   "veranda",
   "entrance"
  ]
 },
 {
  "id": "fire",
  "name": "Fire lounge",
  "area": "Outside",
  "pos": [
   8.5,
   -11.8,
   1.7
  ],
  "links": [
   "pool",
   "entrance"
  ]
 },
 {
  "id": "veranda",
  "name": "Outdoor dining",
  "area": "Outside",
  "pos": [
   -8.4,
   -3.4,
   1.7
  ],
  "links": [
   "pool",
   "car"
  ]
 },
 {
  "id": "entrance",
  "name": "Front door",
  "area": "Outside",
  "pos": [
   5.8,
   -6.2,
   1.7
  ],
  "links": [
   "pool",
   "fire",
   "hall"
  ]
 },
 {
  "id": "hall",
  "name": "Entry hall",
  "area": "Level 1",
  "pos": [
   6.9,
   -1.8,
   1.72
  ],
  "links": [
   "entrance",
   "powder",
   "stair-1"
  ]
 },
 {
  "id": "powder",
  "name": "Powder room",
  "area": "Level 1",
  "pos": [
   6.85,
   2.6,
   1.72
  ],
  "links": [
   "hall"
  ]
 },
 {
  "id": "stair-1",
  "name": "Dining and stair",
  "area": "Level 1",
  "pos": [
   2.75,
   -0.35,
   1.72
  ],
  "links": [
   "hall",
   "living",
   "kitchen",
   "garden-1",
   "landing-2"
  ]
 },
 {
  "id": "living",
  "name": "Living room",
  "area": "Level 1",
  "pos": [
   0.9,
   -3.1,
   1.72
  ],
  "links": [
   "stair-1",
   "garden-1"
  ]
 },
 {
  "id": "kitchen",
  "name": "Kitchen",
  "area": "Level 1",
  "pos": [
   0.6,
   3.3,
   1.72
  ],
  "links": [
   "stair-1"
  ]
 },
 {
  "id": "garden-1",
  "name": "Indoor garden",
  "area": "Level 1",
  "pos": [
   -2.0,
   1.15,
   1.89
  ],
  "links": [
   "stair-1",
   "living",
   "car",
   "library"
  ]
 },
 {
  "id": "car",
  "name": "Car gallery",
  "area": "Level 1",
  "pos": [
   -3.5,
   -3.1,
   1.72
  ],
  "links": [
   "garden-1",
   "library",
   "veranda"
  ]
 },
 {
  "id": "library",
  "name": "Garden lounge",
  "area": "Level 1",
  "pos": [
   -3.9,
   2.5,
   1.72
  ],
  "links": [
   "garden-1",
   "car"
  ]
 },
 {
  "id": "landing-2",
  "name": "Landing lounge",
  "area": "Level 2",
  "pos": [
   2.2,
   -0.5,
   5.57
  ],
  "links": [
   "stair-1",
   "study",
   "bridge-2",
   "landing-3"
  ]
 },
 {
  "id": "study",
  "name": "Study",
  "area": "Level 2",
  "pos": [
   1.3,
   -4.6,
   5.57
  ],
  "links": [
   "landing-2"
  ]
 },
 {
  "id": "bridge-2",
  "name": "Garden bridge",
  "area": "Level 2",
  "pos": [
   -1.9,
   2.3,
   5.57
  ],
  "links": [
   "landing-2",
   "dressing"
  ]
 },
 {
  "id": "dressing",
  "name": "Walk-in wardrobe",
  "area": "Level 2",
  "pos": [
   -4.4,
   2.6,
   5.57
  ],
  "links": [
   "bridge-2",
   "ensuite",
   "bedroom"
  ]
 },
 {
  "id": "ensuite",
  "name": "Ensuite bathroom",
  "area": "Level 2",
  "pos": [
   -8.1,
   2.3,
   5.57
  ],
  "links": [
   "dressing"
  ]
 },
 {
  "id": "bedroom",
  "name": "Bedroom suite",
  "area": "Level 2",
  "pos": [
   -3.8,
   -2.8,
   5.57
  ],
  "links": [
   "dressing"
  ]
 },
 {
  "id": "landing-3",
  "name": "Top of the stair",
  "area": "Level 3",
  "pos": [
   2.6,
   -0.8,
   9.07
  ],
  "links": [
   "landing-2",
   "master",
   "lounge-3",
   "bath"
  ]
 },
 {
  "id": "master",
  "name": "Master suite",
  "area": "Level 3",
  "pos": [
   2.8,
   -2.6,
   9.07
  ],
  "links": [
   "landing-3",
   "lounge-3"
  ]
 },
 {
  "id": "lounge-3",
  "name": "Sky lounge",
  "area": "Level 3",
  "pos": [
   0.6,
   1.8,
   9.07
  ],
  "links": [
   "landing-3",
   "master",
   "bridge-3"
  ]
 },
 {
  "id": "bridge-3",
  "name": "Sky bridge",
  "area": "Level 3",
  "pos": [
   -1.9,
   2.3,
   9.07
  ],
  "links": [
   "lounge-3",
   "terrace"
  ]
 },
 {
  "id": "bath",
  "name": "Master bathroom",
  "area": "Level 3",
  "pos": [
   7.3,
   2.4,
   9.07
  ],
  "links": [
   "landing-3"
  ]
 },
 {
  "id": "terrace",
  "name": "Roof terrace",
  "area": "Level 3",
  "pos": [
   -5.0,
   -0.5,
   8.99
  ],
  "links": [
   "bridge-3",
   "terrace-edge"
  ]
 },
 {
  "id": "terrace-edge",
  "name": "Terrace edge",
  "area": "Level 3",
  "pos": [
   -6.0,
   -6.9,
   8.99
  ],
  "links": [
   "terrace"
  ]
 }
];
